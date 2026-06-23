"use client"

/**
 * Production Run/Inspection Program Page
 * Real-time vision inspection with live camera feed, GPIO control, and statistics
 */

import { useState, useRef, useEffect, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Play,
  Pause,
  Square,
  Camera,
  CheckCircle2,
  XCircle,
  Activity,
  Clock,
  TrendingUp,
  Zap,
  ArrowLeft,
  Settings,
  Download,
  AlertCircle,
  Target,
  RefreshCw,
  History,
  ImageIcon,
  LayoutTemplate,
} from "lucide-react"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Label } from "@/components/ui/label"
import type { ToolTemplateSummary } from "@/types"
import { api } from "@/lib/api"
import { storage, Program } from "@/lib/storage"
import {
  captureOptionsFromConfig,
  findToolForResult,
  getToolsForRun,
  normalizeRunProgram,
} from "@/lib/run-program"
import {
  getInspectionTransport,
  isRestFallbackEligible,
  mapWsInspectionToResult,
  overallConfidenceFromToolResults,
} from "@/lib/inspection-transport"
import { ws } from "@/lib/websocket"
import type { InspectionResultEvent } from "@/types"
import {
  processInspection,
  extractMasterFeatures,
  enableHighQualityCanvasScaling,
  rawBase64ToImageDataUrl,
  resolveToolRoisForImagePixels,
  resolveToolRoisForImageSize,
  mapRoiImageToCanvas,
} from "@/lib/inspection-engine"
import type {
  ToolConfig,
  ToolResult,
  InspectionResult as TypedInspectionResult,
  OutputAssignment,
  OutputCondition,
} from "@/types"

// ==================== INTERFACES ====================

interface InspectionResult {
  id: string
  timestamp: Date
  programId: string | number
  status: "OK" | "NG"
  overallConfidence: number
  processingTime: number
  toolResults: ToolResult[]
  image: string
  positionOffset?: { x: number; y: number }
  /** Set when /api/inspection/run-once persisted the row (skip duplicate POST /api/inspections). */
  resultId?: number | null
}

interface Statistics {
  totalInspected: number
  passed: number
  failed: number
  passRate: number
  avgProcessingTime: number
  currentCycleTime: number
  avgConfidence: number
  uptime: number
}

interface GPIOOutput {
  pin: string
  name: string
  state: boolean
  color: string
  condition: OutputCondition
}

/** Row from GET /api/inspections/:programId */
interface DbInspectionRecord {
  id: number
  program_id: number
  timestamp: string
  overall_status: "OK" | "NG"
  processing_time_ms: number
  image_path: string | null
  trigger_type?: string
  tool_results?: ToolResult[]
}

// ==================== MAIN COMPONENT ====================

export default function RunInspectionPage() {
  // ========== STATE MANAGEMENT ==========
  
  // Program Management
  const [programs, setPrograms] = useState<Program[]>([])
  const [selectedProgramId, setSelectedProgramId] = useState<string>("")
  const [selectedProgram, setSelectedProgram] = useState<Program | null>(null)

  /** Full program vs tool template + program master */
  const [runMode, setRunMode] = useState<"program" | "template">("program")
  const [templateSummaries, setTemplateSummaries] = useState<ToolTemplateSummary[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string>("")
  const [templatesLoading, setTemplatesLoading] = useState(false)
  /** Tools from template, ROIs resolved to master pixels (browser fallback). */
  const templateToolsForRunRef = useRef<ToolConfig[] | null>(null)
  const [activeToolsForRun, setActiveToolsForRun] = useState<ToolConfig[]>([])
  const [templatePrepReady, setTemplatePrepReady] = useState(true)
  const [templatePrepError, setTemplatePrepError] = useState<string | null>(null)
  
  // Inspection State
  const [isRunning, setIsRunning] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [currentStatus, setCurrentStatus] = useState<"IDLE" | "RUNNING" | "OK" | "NG">("IDLE")
  
  // Image & Results
  const [currentFrame, setCurrentFrame] = useState<string>("")
  const [currentResult, setCurrentResult] = useState<InspectionResult | null>(null)
  const [recentResults, setRecentResults] = useState<InspectionResult[]>([])
  
  // Statistics
  const [statistics, setStatistics] = useState<Statistics>({
    totalInspected: 0,
    passed: 0,
    failed: 0,
    passRate: 0,
    avgProcessingTime: 0,
    currentCycleTime: 0,
    avgConfidence: 0,
    uptime: 0,
  })
  
  // GPIO Outputs
  const [gpioOutputs, setGpioOutputs] = useState<GPIOOutput[]>([])
  
  // Master Features
  const [masterFeatures, setMasterFeatures] = useState<Record<string, any>>({})
  
  // Loading State
  const [isLoadingPrograms, setIsLoadingPrograms] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [dbInspectionHistory, setDbInspectionHistory] = useState<DbInspectionRecord[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyPreview, setHistoryPreview] = useState<{
    programId: number
    resultId: number
    status: "OK" | "NG"
    timestamp: string
    processingMs: number
  } | null>(null)
  
  // Refs
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const inspectionTimerRef = useRef<NodeJS.Timeout | null>(null)
  const startTimeRef = useRef<number>(0)
  const wsConnectedRef = useRef<boolean>(false)
  const liveFeedSubscribeCancelRef = useRef<(() => void) | null>(null)
  const performInspectionRef = useRef<() => Promise<void>>(async () => {})
  const runStateRef = useRef({ isRunning: false, isPaused: false })
  const inspectionInFlightRef = useRef(false)
  const resultHoldUntilRef = useRef(0)
  const wsContinuousRef = useRef(false)
  const pendingWsInspectionRef = useRef<{
    resolve: (r: InspectionResult) => void
    reject: (e: Error) => void
    timeoutId: ReturnType<typeof setTimeout>
  } | null>(null)
  const inspectionTransport = getInspectionTransport()
  const RESULT_HOLD_MS = 1500

  useEffect(() => {
    runStateRef.current = { isRunning, isPaused }
  }, [isRunning, isPaused])

  // ========== LOAD PROGRAMS ON MOUNT ==========
  
  useEffect(() => {
    loadPrograms()
    void loadToolTemplates()
  }, [])

  const loadToolTemplates = async () => {
    setTemplatesLoading(true)
    try {
      const list = await api.getToolTemplates()
      setTemplateSummaries(list)
    } catch (e) {
      console.warn("Could not load tool templates:", e)
      setTemplateSummaries([])
    } finally {
      setTemplatesLoading(false)
    }
  }
  
  const loadPrograms = async () => {
    setIsLoadingPrograms(true)
    setLoadError(null)
    
    try {
      let loadedPrograms: Program[] = []
      
      // Try to load from backend API first
      try {
        const response = await fetch('/api/programs', {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
          },
        })
        
        if (response.ok) {
          const data = await response.json()
          const rawList: unknown[] = Array.isArray(data.programs)
            ? data.programs
            : Array.isArray(data)
              ? data
              : []
          loadedPrograms = rawList
            .map((p) => normalizeRunProgram(p))
            .filter((p): p is Program => p !== null)
          console.log(`Loaded ${loadedPrograms.length} programs from API`)
        } else {
          console.warn('API response not OK, falling back to localStorage')
          throw new Error('API failed')
        }
      } catch (apiError) {
        console.warn('Failed to load from API, trying localStorage:', apiError)
        
        // Fallback to localStorage
        loadedPrograms = storage
          .getAllPrograms()
          .map((p) => normalizeRunProgram(p))
          .filter((p): p is Program => p !== null)
        console.log(`Loaded ${loadedPrograms.length} programs from localStorage`)
      }
      
      // Filter out invalid programs
      const validPrograms = loadedPrograms.filter(p => 
        p && p.id && p.name && p.config
      )
      
      if (validPrograms.length < loadedPrograms.length) {
        console.warn(`Filtered out ${loadedPrograms.length - validPrograms.length} invalid programs`)
      }
      
      setPrograms(validPrograms)
      
      if (validPrograms.length === 0) {
        setLoadError("No inspection programs found. Please create a program first.")
        setIsLoadingPrograms(false)
        return
      }
      
      const urlParams = new URLSearchParams(window.location.search)
      const programId = urlParams.get("id")
      const templateId = urlParams.get("template")

      if (templateId) {
        setRunMode("template")
        setSelectedTemplateId(templateId)
      }

      if (programId && validPrograms.find(p => p.id === programId || p.id === String(programId))) {
        setSelectedProgramId(String(programId))
        console.log(`Auto-selected program from URL: ${programId}`)
      } else if (validPrograms.length > 0) {
        setSelectedProgramId(validPrograms[0].id)
        console.log(`Auto-selected first program: ${validPrograms[0].id}`)
      }
      
      setIsLoadingPrograms(false)
      
    } catch (error) {
      console.error("Failed to load programs:", error)
      setLoadError("Failed to load programs. Please try again.")
      setIsLoadingPrograms(false)
    }
  }
  
  // ========== LOAD SELECTED PROGRAM ==========
  
  const prepareTemplateRunContext = useCallback(
    async (program: Program, templateId: number) => {
      const programIdNum = parseInt(String(program.id), 10)
      if (Number.isNaN(programIdNum)) return
      setTemplatePrepReady(false)
      setTemplatePrepError(null)
      try {
        const imgRes = await api.getMasterImage(programIdNum)
        if (!imgRes.image) {
          templateToolsForRunRef.current = null
          setActiveToolsForRun([])
          setMasterFeatures({})
          setTemplatePrepError("Master image not available for this program")
          setTemplatePrepReady(false)
          return
        }
        const scaled = await api.getToolTemplateForProgram(templateId, programIdNum)
        const toolsForMaster = scaled.tools as ToolConfig[]
        templateToolsForRunRef.current = toolsForMaster
        setActiveToolsForRun(toolsForMaster)
        const features = await extractMasterFeatures(imgRes.image, toolsForMaster)
        setMasterFeatures(features)
        setTemplatePrepReady(true)
        setTemplatePrepError(null)
      } catch (err) {
        console.error("Failed to prepare template run context:", err)
        templateToolsForRunRef.current = null
        setActiveToolsForRun([])
        setMasterFeatures({})
        setTemplatePrepError("Failed to load template for run")
        setTemplatePrepReady(false)
      }
    },
    []
  )

  useEffect(() => {
    if (!selectedProgramId) return
    const program = programs.find(
      (p) => String(p.id) === String(selectedProgramId)
    )
    if (!program) return

    setSelectedProgram(program)
    initializeGPIOOutputs(program.config.outputs)

    const ownedTplId = (program.config as { toolTemplateId?: number }).toolTemplateId
    if (ownedTplId != null && !Number.isNaN(Number(ownedTplId))) {
      setSelectedTemplateId(String(ownedTplId))
    }

    const templateIdForRun =
      ownedTplId != null && !Number.isNaN(Number(ownedTplId))
        ? Number(ownedTplId)
        : selectedTemplateId
          ? parseInt(selectedTemplateId, 10)
          : NaN

    if (runMode === "template" && !Number.isNaN(templateIdForRun)) {
      void prepareTemplateRunContext(program, templateIdForRun)
      return
    }

    templateToolsForRunRef.current = null
    setTemplatePrepReady(true)
    setTemplatePrepError(null)
    setActiveToolsForRun((program.config.tools as ToolConfig[]) ?? [])

    if (program.config.masterImage && program.config.tools.length > 0) {
      const rawMaster = program.config.masterImage
      const baseTools = program.config.tools as ToolConfig[]
      void resolveToolRoisForImagePixels(
        rawMaster,
        baseTools,
        (program.config as { toolsRoiSpace?: string }).toolsRoiSpace
      )
        .then((toolsForMaster) => extractMasterFeatures(rawMaster, toolsForMaster))
        .then((features) => {
          setMasterFeatures(features)
        })
        .catch((err) => console.error("Failed to extract master features:", err))
    }
  }, [
    selectedProgramId,
    selectedTemplateId,
    runMode,
    programs,
    prepareTemplateRunContext,
  ])

  const refreshDbHistory = useCallback(async () => {
    const pid = parseInt(String(selectedProgramId), 10)
    if (isNaN(pid)) {
      setDbInspectionHistory([])
      return
    }
    setHistoryLoading(true)
    setHistoryError(null)
    try {
      const { history } = await api.getInspectionHistory(pid, { limit: 80 })
      setDbInspectionHistory(history as DbInspectionRecord[])
    } catch (e) {
      console.error("Failed to load inspection history:", e)
      setHistoryError("Could not load saved history")
      setDbInspectionHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }, [selectedProgramId])

  useEffect(() => {
    refreshDbHistory()
  }, [refreshDbHistory])
  
  const initializeGPIOOutputs = (outputs: OutputAssignment) => {
    const gpios: GPIOOutput[] = [
      { pin: "OUT1", name: "BUSY", state: false, color: "#eab308", condition: outputs.OUT1 },
      { pin: "OUT2", name: "OK Signal", state: false, color: "#10b981", condition: outputs.OUT2 },
      { pin: "OUT3", name: "NG Signal", state: false, color: "#ef4444", condition: outputs.OUT3 },
      { pin: "OUT4", name: "Custom 1", state: false, color: "#3b82f6", condition: outputs.OUT4 },
      { pin: "OUT5", name: "Custom 2", state: false, color: "#8b5cf6", condition: outputs.OUT5 },
      { pin: "OUT6", name: "Custom 3", state: false, color: "#ec4899", condition: outputs.OUT6 },
      { pin: "OUT7", name: "Custom 4", state: false, color: "#14b8a6", condition: outputs.OUT7 },
      { pin: "OUT8", name: "Custom 5", state: false, color: "#f97316", condition: outputs.OUT8 },
    ]
    setGpioOutputs(gpios)
  }
  
  // ========== WEBSOCKET CONNECTION ==========
  
  useEffect(() => {
    if (isRunning && !isPaused && selectedProgram) {
      connectWebSocket()
    } else {
      disconnectWebSocket()
    }
    
    return () => disconnectWebSocket()
  }, [isRunning, isPaused, selectedProgram])
  
  const connectWebSocket = () => {
    if (wsConnectedRef.current) return
    
    try {
      wsConnectedRef.current = true
      
      ws.on("live_frame", handleLiveFrame)
      ws.on("inspection_result", handleInspectionResult)
      ws.on("error", handleWSError)
      
      liveFeedSubscribeCancelRef.current?.()
      const liveCaptureOpts = selectedProgram
        ? captureOptionsFromConfig(selectedProgram.config)
        : undefined
      liveFeedSubscribeCancelRef.current = ws.subscribeLiveFeedWhenReady(
        4,
        true,
        liveCaptureOpts
      )
      
      if (process.env.NODE_ENV === "development") {
        console.log("WebSocket: live feed subscription scheduled")
      }
    } catch (error) {
      console.error("WebSocket connection failed:", error)
      wsConnectedRef.current = false
    }
  }
  
  const disconnectWebSocket = () => {
    if (!wsConnectedRef.current) return
    
    try {
      liveFeedSubscribeCancelRef.current?.()
      liveFeedSubscribeCancelRef.current = null
      ws.off("live_frame", handleLiveFrame)
      ws.off("inspection_result", handleInspectionResult)
      ws.off("error", handleWSError)
      ws.unsubscribeLiveFeed()
      ws.disconnect()
      wsConnectedRef.current = false
      if (process.env.NODE_ENV === "development") {
        console.log("WebSocket disconnected")
      }
    } catch (error) {
      console.error("Error disconnecting WebSocket:", error)
    }
  }
  
  const handleLiveFrame = useCallback((data: any) => {
    if (data.image) {
      setCurrentFrame(data.image)
      if (Date.now() < resultHoldUntilRef.current) return
      if (currentStatus === "RUNNING" || currentStatus === "IDLE") {
        drawFrame(data.image)
      }
    }
  }, [currentStatus])
  
  const applyInspectionResult = useCallback(
    (result: InspectionResult, opts?: { clientDroveBusyPin?: boolean }) => {
      const clientDroveBusyPin = opts?.clientDroveBusyPin ?? false
      setCurrentResult(result)
      setRecentResults((prev) => [result, ...prev].slice(0, 20))
      updateStatistics(result)
      updateGPIOFromResult(result, {
        writeHardware: typeof result.resultId !== "number",
      })
      drawInspectionResult(result)
      setCurrentStatus(result.status)
      resultHoldUntilRef.current = Date.now() + RESULT_HOLD_MS
      saveInspectionResult(result)
      setTimeout(() => {
        const { isRunning: running, isPaused: paused } = runStateRef.current
        if (running && !paused) {
          setCurrentStatus("RUNNING")
        }
        if (clientDroveBusyPin) {
          updateGPIOOutput("OUT1", false)
        } else {
          updateGPIOOutput("OUT1", false, { writeHardware: false })
        }
      }, 500)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable helpers below definition
    [selectedProgram, activeToolsForRun]
  )

  const handleInspectionResult = useCallback(
    (data: InspectionResultEvent) => {
      if (!selectedProgram) return
      try {
        const result = mapWsInspectionToResult(
          data,
          String(selectedProgram.id)
        ) as InspectionResult
        const pending = pendingWsInspectionRef.current
        if (pending) {
          pending.resolve(result)
          clearTimeout(pending.timeoutId)
          pendingWsInspectionRef.current = null
        } else if (wsContinuousRef.current || data.inspectionCount != null) {
          applyInspectionResult(result, { clientDroveBusyPin: false })
        }
      } catch (err) {
        pendingWsInspectionRef.current?.reject(
          err instanceof Error ? err : new Error(String(err))
        )
        if (pendingWsInspectionRef.current) {
          clearTimeout(pendingWsInspectionRef.current.timeoutId)
          pendingWsInspectionRef.current = null
        }
      } finally {
        if (!pendingWsInspectionRef.current) {
          inspectionInFlightRef.current = false
        }
      }
    },
    [selectedProgram, applyInspectionResult]
  )
  
  const handleWSError = useCallback((data: any) => {
    console.error("WebSocket error:", data)
  }, [])
  
  // ========== INSPECTION TRIGGER SYSTEM ==========
  
  const inspectionReady =
    runMode === "program"
      ? !!selectedProgram
      : !!selectedProgram && !!selectedTemplateId && templatePrepReady

  const canStart =
    inspectionReady &&
    (runMode === "program" || templatePrepReady) &&
    !templatePrepError

  const waitForWsInspection = useCallback((): Promise<InspectionResult> => {
    return new Promise((resolve, reject) => {
      const timeoutId = setTimeout(() => {
        pendingWsInspectionRef.current = null
        reject(new Error("WebSocket inspection timeout"))
      }, 45000)
      pendingWsInspectionRef.current = { resolve, reject, timeoutId }
    })
  }, [])

  useEffect(() => {
    if (!isRunning || isPaused || !inspectionReady || !selectedProgram) {
      if (inspectionTimerRef.current) {
        clearInterval(inspectionTimerRef.current)
        inspectionTimerRef.current = null
      }
      return
    }

    const useWsInternal =
      inspectionTransport === "ws" &&
      selectedProgram.config.triggerType === "internal"

    if (useWsInternal) {
      console.log("Internal trigger: WebSocket continuous inspection (backend cadence)")
      return () => {}
    }

    if (selectedProgram.config.triggerType === "internal") {
      const interval = selectedProgram.config.triggerInterval || 2000

      void performInspectionRef.current()
      inspectionTimerRef.current = setInterval(() => {
        void performInspectionRef.current()
      }, interval)

      console.log(`Internal trigger started: ${interval}ms interval`)
    } else {
      console.log("External trigger mode - waiting for GPIO signal")
    }

    return () => {
      if (inspectionTimerRef.current) {
        clearInterval(inspectionTimerRef.current)
        inspectionTimerRef.current = null
      }
    }
  }, [isRunning, isPaused, selectedProgram, inspectionReady, runMode, inspectionTransport])
  
  // ========== INSPECTION PROCESSING ==========
  
  const restResponseToResult = (
    resp: {
      resultId: number | null
      status: "OK" | "NG"
      toolResults: ToolResult[]
      processingTimeMs: number
      image?: string
    }
  ): InspectionResult => {
    const pos = resp.toolResults.find((t) => t.tool_type === "position_adjust")
    return {
      id: String(resp.resultId ?? Date.now()),
      timestamp: new Date(),
      programId: selectedProgram!.id,
      status: resp.status,
      overallConfidence: overallConfidenceFromToolResults(resp.toolResults),
      processingTime: resp.processingTimeMs,
      toolResults: resp.toolResults,
      image: resp.image
        ? resp.image.startsWith("data:")
          ? resp.image
          : rawBase64ToImageDataUrl(resp.image)
        : "",
      positionOffset: pos?.offset
        ? { x: pos.offset.dx, y: pos.offset.dy }
        : undefined,
      resultId: resp.resultId ?? undefined,
    }
  }

  const runWsInspectionOnce = async (programIdNum: number): Promise<InspectionResult> => {
    if (!ws.connected()) {
      ws.connect()
      await new Promise((r) => setTimeout(r, 500))
    }
    const waitPromise = waitForWsInspection()
    const templateIdNum =
      runMode === "template" ? parseInt(selectedTemplateId, 10) : undefined
    ws.startInspection(
      programIdNum,
      false,
      Number.isNaN(templateIdNum as number) ? undefined : templateIdNum
    )
    return waitPromise
  }

  const performInspection = async () => {
    if (!selectedProgram) {
      console.warn("Cannot perform inspection: no program")
      return
    }
    if (runMode === "template" && !selectedTemplateId) {
      console.warn("Cannot perform inspection: no template selected")
      return
    }
    if (inspectionInFlightRef.current) return

    inspectionInFlightRef.current = true
    let clientDroveBusyPin = false

    try {
      setCurrentStatus("RUNNING")
      updateGPIOOutput("OUT1", true, { writeHardware: false })

      const startTime = performance.now()
      const programIdNum = parseInt(String(selectedProgram.id), 10)
      if (Number.isNaN(programIdNum)) {
        throw new Error("Invalid program id")
      }

      let result: InspectionResult
      const useWsPrimary = inspectionTransport === "ws"
      const useRestFirst =
        inspectionTransport === "rest" ||
        inspectionTransport === "rest-with-ws-fallback"

      if (useWsPrimary) {
        result = await runWsInspectionOnce(programIdNum)
        inspectionInFlightRef.current = false
        applyInspectionResult(result, { clientDroveBusyPin: false })
        return
      }

      if (useRestFirst) {
        try {
          const resp =
            runMode === "template"
              ? await api.runInspectionWithTemplate({
                  templateId: parseInt(selectedTemplateId, 10),
                  programId: programIdNum,
                  triggerType: selectedProgram.config.triggerType || "internal",
                  includeImage: true,
                  persist: true,
                })
              : await api.runInspectionOnce({
                  programId: programIdNum,
                  triggerType: selectedProgram.config.triggerType || "internal",
                  includeImage: true,
                  persist: true,
                })
          result = restResponseToResult(resp)
        } catch (serverErr) {
          if (
            inspectionTransport === "rest-with-ws-fallback" &&
            isRestFallbackEligible(serverErr)
          ) {
            console.warn("REST inspection failed, trying WebSocket fallback:", serverErr)
            result = await runWsInspectionOnce(programIdNum)
            inspectionInFlightRef.current = false
            applyInspectionResult(result, { clientDroveBusyPin: false })
            return
          }
          console.warn("Server inspection unavailable, using browser engine:", serverErr)
          clientDroveBusyPin = true
          updateGPIOOutput("OUT1", true)

          const captured = await api.captureImage(
            captureOptionsFromConfig(selectedProgram.config)
          )

          const baseTools = getToolsForRun(
            runMode,
            selectedProgram,
            templateToolsForRunRef.current
          )

          const toolsForFrame = await resolveToolRoisForImagePixels(
            captured.image,
            baseTools,
            runMode === 'template'
              ? 'native'
              : (selectedProgram.config as { toolsRoiSpace?: string }).toolsRoiSpace
          )
          const local = await processInspection(captured.image, toolsForFrame, {
            masterFeatures,
            debugMode: false,
          })
          local.programId = selectedProgram.id
          local.processingTime = performance.now() - startTime
          result = local as InspectionResult
        }
      } else {
        throw new Error("Unknown inspection transport")
      }

      applyInspectionResult(result, { clientDroveBusyPin })
    } catch (error) {
      console.error("Inspection failed:", error)
      setCurrentStatus("NG")
      updateGPIOOutput("OUT3", true)
      setTimeout(() => {
        if (clientDroveBusyPin) {
          updateGPIOOutput("OUT1", false)
        } else {
          updateGPIOOutput("OUT1", false, { writeHardware: false })
        }
        updateGPIOOutput("OUT3", false)
      }, 500)
    } finally {
      inspectionInFlightRef.current = false
      if (pendingWsInspectionRef.current) {
        clearTimeout(pendingWsInspectionRef.current.timeoutId)
        pendingWsInspectionRef.current = null
      }
    }
  }

  performInspectionRef.current = performInspection

  const updateStatistics = (result: InspectionResult) => {
    setStatistics(prev => {
      const total = prev.totalInspected + 1
      const passed = result.status === "OK" ? prev.passed + 1 : prev.passed
      const failed = result.status === "NG" ? prev.failed + 1 : prev.failed
      const passRate = (passed / total) * 100
      
      const totalTime = prev.avgProcessingTime * prev.totalInspected + result.processingTime
      const avgProcessingTime = totalTime / total
      
      const totalConfidence = prev.avgConfidence * prev.totalInspected + result.overallConfidence
      const avgConfidence = totalConfidence / total
      
      const uptime = Date.now() - startTimeRef.current
      
      return {
        totalInspected: total,
        passed,
        failed,
        passRate,
        avgProcessingTime,
        currentCycleTime: result.processingTime,
        avgConfidence,
        uptime,
      }
    })
  }
  
  const saveInspectionResult = async (result: InspectionResult) => {
    if (!selectedProgram) return

    if (typeof result.resultId === "number") {
      void refreshDbHistory()
      return
    }

    const programIdNum = parseInt(String(selectedProgram.id))
    if (!isNaN(programIdNum)) {
      fetch("/api/inspections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          program_id: programIdNum,
          status: result.status,
          processing_time_ms: result.processingTime,
          tool_results: result.toolResults,
          trigger_type: selectedProgram.config.triggerType || "internal",
          image: result.image ?? null,
        }),
      })
        .then(res => {
          if (res.ok) void refreshDbHistory()
        })
        .catch(err => console.error("Failed to persist inspection to DB:", err))
    }
  }
  
  // ========== GPIO OUTPUT CONTROL ==========
  
  const updateGPIOFromResult = (
    result: InspectionResult,
    opts?: { writeHardware?: boolean }
  ) => {
    const writeHw = opts?.writeHardware !== false
    // Update outputs based on result
    gpioOutputs.forEach(gpio => {
      let shouldActivate = false
      
      switch (gpio.condition) {
        case "OK":
          shouldActivate = result.status === "OK"
          break
        case "NG":
          shouldActivate = result.status === "NG"
          break
        case "Always ON":
          shouldActivate = true
          break
        case "Always OFF":
          shouldActivate = false
          break
        case "Not Used":
          shouldActivate = false
          break
      }
      
      if (gpio.pin === "OUT2" && result.status === "OK") {
        // Pulse OK signal
        updateGPIOOutput(gpio.pin, true, { writeHardware: writeHw })
        setTimeout(() => updateGPIOOutput(gpio.pin, false, { writeHardware: writeHw }), 300)
      } else if (gpio.pin === "OUT3" && result.status === "NG") {
        // Pulse NG signal
        updateGPIOOutput(gpio.pin, true, { writeHardware: writeHw })
        setTimeout(() => updateGPIOOutput(gpio.pin, false, { writeHardware: writeHw }), 300)
      } else if (gpio.pin !== "OUT1" && gpio.pin !== "OUT2" && gpio.pin !== "OUT3") {
        updateGPIOOutput(gpio.pin, shouldActivate, { writeHardware: writeHw })
      }
    })
  }
  
  const updateGPIOOutput = (
    pin: string,
    state: boolean,
    opts?: { writeHardware?: boolean }
  ) => {
    const writeHardware = opts?.writeHardware !== false

    setGpioOutputs(prev =>
      prev.map(gpio => (gpio.pin === pin ? { ...gpio, state } : gpio))
    )

    if (!writeHardware) return

    // Send to backend/hardware
    fetch("/api/gpio/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin, value: state }),
    }).catch(err => console.error("GPIO write failed:", err))
  }
  
  // ========== CANVAS DRAWING ==========
  
  const drawFrame = (frameBase64: string) => {
    const canvas = canvasRef.current
    if (!canvas) return
    
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    
    const img = new Image()
    img.onload = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      enableHighQualityCanvasScaling(ctx)
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    }
    img.src = frameBase64.startsWith("data:") ? frameBase64 : rawBase64ToImageDataUrl(frameBase64)
  }
  
  const drawInspectionResult = (result: InspectionResult) => {
    const canvas = canvasRef.current
    if (!canvas) return
    
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    
    const img = new Image()
    img.onload = () => {
      // Clear and draw image
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)

      const nw = img.naturalWidth || img.width
      const nh = img.naturalHeight || img.height
      const cw = canvas.width
      const ch = canvas.height

      // Draw ROI overlays (all tool types — ROIs aligned to image then canvas)
      const overlayTools =
        activeToolsForRun.length > 0
          ? activeToolsForRun
          : selectedProgram
            ? (selectedProgram.config.tools as ToolConfig[])
            : []
      if (overlayTools.length > 0) {
        const toolsInImage = resolveToolRoisForImageSize(overlayTools, nw, nh)
        toolsInImage.forEach((tool: ToolConfig) => {
          const toolResult =
            result.toolResults.find((tr) => tr.name === tool.name) ??
            result.toolResults.find((tr) => tr.tool_type === tool.type)
          if (!toolResult) return

          const offsetX = result.positionOffset?.x || 0
          const offsetY = result.positionOffset?.y || 0
          const roiImg = {
            x: tool.roi.x + offsetX,
            y: tool.roi.y + offsetY,
            width: tool.roi.width,
            height: tool.roi.height,
          }
          const roi = mapRoiImageToCanvas(roiImg, nw, nh, cw, ch)

          const color = toolResult.status === "OK" ? "#10b981" : "#ef4444"

          ctx.strokeStyle = color
          ctx.lineWidth = 3
          ctx.strokeRect(roi.x, roi.y, roi.width, roi.height)

          const labelText = `${tool.name}: ${toolResult.matching_rate.toFixed(1)}%`
          ctx.font = "bold 14px sans-serif"
          const textWidth = ctx.measureText(labelText).width

          ctx.fillStyle = color
          ctx.fillRect(roi.x, roi.y - 25, textWidth + 16, 25)

          ctx.fillStyle = "#ffffff"
          ctx.fillText(labelText, roi.x + 8, roi.y - 7)
        })
      }
      
      // Draw overall status
      const statusColor = result.status === "OK" ? "#10b981" : "#ef4444"
      ctx.fillStyle = statusColor + "30"
      ctx.fillRect(10, 10, 200, 60)
      
      ctx.strokeStyle = statusColor
      ctx.lineWidth = 3
      ctx.strokeRect(10, 10, 200, 60)
      
      ctx.fillStyle = statusColor
      ctx.font = "bold 32px sans-serif"
      ctx.fillText(result.status, 20, 50)
    }
    img.src = result.image.startsWith("data:") ? result.image : rawBase64ToImageDataUrl(result.image)
  }
  
  // ========== CONTROL FUNCTIONS ==========
  
  const handleStart = () => {
    if (!selectedProgramId) {
      alert("Please select a program (master image host)")
      return
    }
    if (runMode === "template" && !selectedTemplateId) {
      alert("Please select a tool configuration template")
      return
    }
    if (!canStart) {
      alert(templatePrepError ?? "Template is still loading — wait for preparation to finish")
      return
    }

    setIsRunning(true)
    setIsPaused(false)
    setCurrentStatus("RUNNING")
    startTimeRef.current = Date.now()
    
    // Reset statistics
    setStatistics({
      totalInspected: 0,
      passed: 0,
      failed: 0,
      passRate: 0,
      avgProcessingTime: 0,
      currentCycleTime: 0,
      avgConfidence: 0,
      uptime: 0,
    })
    
    setRecentResults([])

    if (
      inspectionTransport === "ws" &&
      selectedProgram?.config.triggerType === "internal"
    ) {
      const programIdNum = parseInt(String(selectedProgram.id), 10)
      if (!Number.isNaN(programIdNum)) {
        wsContinuousRef.current = true
        try {
          if (!ws.connected()) ws.connect()
          const tid =
            runMode === "template"
              ? parseInt(selectedTemplateId, 10)
              : undefined
          ws.startInspection(
            programIdNum,
            true,
            Number.isNaN(tid as number) ? undefined : tid
          )
        } catch (e) {
          console.error("Failed to start WS continuous inspection:", e)
        }
      }
    }
  }
  
  const handleStop = () => {
    setIsRunning(false)
    setIsPaused(false)
    setCurrentStatus("IDLE")
    inspectionInFlightRef.current = false
    resultHoldUntilRef.current = 0
    wsContinuousRef.current = false

    if (inspectionTimerRef.current) {
      clearInterval(inspectionTimerRef.current)
      inspectionTimerRef.current = null
    }

    try {
      if (ws.connected()) ws.stopInspection()
    } catch {
      /* ignore */
    }

    gpioOutputs.forEach((gpio) => {
      updateGPIOOutput(gpio.pin, false)
    })
  }
  
  const handlePause = () => {
    setIsPaused(!isPaused)
  }
  
  const handleManualTrigger = () => {
    if (!isRunning || isPaused) {
      return
    }
    
    // Manually trigger an inspection
    performInspection()
  }
  
  const handleProgramChange = (programId: string) => {
    if (isRunning) {
      alert("Stop inspection before changing programs")
      return
    }
    setSelectedProgramId(programId)
  }

  const handleRunModeChange = (mode: "program" | "template") => {
    if (isRunning) {
      alert("Stop inspection before changing run mode")
      return
    }
    setRunMode(mode)
    if (mode === "program") {
      setSelectedTemplateId("")
    }
  }

  const handleTemplateChange = (templateId: string) => {
    if (isRunning) {
      alert("Stop inspection before changing template")
      return
    }
    setSelectedTemplateId(templateId)
  }
  
  const exportResults = () => {
    const data = {
      program: selectedProgram?.name,
      statistics,
      recentResults: recentResults.map(r => ({
        id: r.id,
        timestamp: r.timestamp.toISOString(),
        status: r.status,
        processingTime: r.processingTime,
        confidence: r.overallConfidence,
      })),
      exportedAt: new Date().toISOString(),
    }
    
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `inspection-results-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  
  // ========== RENDER ==========
  
  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Header */}
      <div className="border-b px-6 py-4 bg-slate-900">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => (window.location.href = "/")}
              className="text-slate-400 hover:text-white"
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
            <div>
              <h2 className="text-2xl font-bold text-white flex items-center gap-3">
                Run Inspection Program
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={loadPrograms}
                  disabled={isLoadingPrograms || isRunning}
                  className="text-slate-400 hover:text-white"
                  title="Reload programs"
                >
                  <RefreshCw className={`h-4 w-4 ${isLoadingPrograms ? 'animate-spin' : ''}`} />
                </Button>
              </h2>
              <p className="text-sm text-slate-400 mt-1">
                {isLoadingPrograms 
                  ? "Loading programs..." 
                  : runMode === "template"
                    ? selectedProgram && selectedTemplateId
                      ? `Template run: ${templateSummaries.find(t => String(t.id) === selectedTemplateId)?.name ?? "template"} on ${selectedProgram.name}`
                      : "Select a program (master) and a tool template"
                    : selectedProgram 
                      ? selectedProgram.name 
                      : programs.length === 0 
                        ? "No programs available - Please create one first"
                        : "Select a program to begin"}
              </p>
            </div>
          </div>
          
          <div className="flex items-center gap-4">
            {/* Status Badge */}
            {currentStatus === "IDLE" && <Badge variant="outline">IDLE</Badge>}
            {currentStatus === "RUNNING" && (
              <Badge className="bg-blue-600">
                <Activity className="h-3 w-3 mr-1 animate-pulse" />
                RUNNING
              </Badge>
            )}
            {currentStatus === "OK" && (
              <Badge className="bg-green-600">
                <CheckCircle2 className="h-3 w-3 mr-1" />
                PASS
              </Badge>
            )}
            {currentStatus === "NG" && (
              <Badge className="bg-red-600">
                <XCircle className="h-3 w-3 mr-1" />
                FAIL
              </Badge>
            )}
            
            {/* Statistics Summary */}
            <div className="text-right">
              <div className="text-sm text-slate-400">Total Inspections</div>
              <div className="text-2xl font-bold text-white">{statistics.totalInspected}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Live Feed (60%) */}
        <div className="flex-1 flex flex-col p-6">
          {/* Control Bar */}
          <Card className="mb-4 bg-slate-900 border-slate-800">
            <CardContent className="p-4">
              {/* Loading State */}
              {isLoadingPrograms && (
                <div className="flex items-center gap-3 text-slate-400">
                  <Activity className="h-5 w-5 animate-spin" />
                  <span>Loading programs...</span>
                </div>
              )}
              
              {/* Error State */}
              {!isLoadingPrograms && loadError && (
                <div className="flex items-center gap-3 text-red-400">
                  <AlertCircle className="h-5 w-5" />
                  <span>{loadError}</span>
                  <Button 
                    onClick={loadPrograms} 
                    variant="outline" 
                    size="sm"
                    className="ml-auto"
                  >
                    Retry
                  </Button>
                </div>
              )}
              
              {/* Normal State */}
              {!isLoadingPrograms && !loadError && (
                <div className="flex flex-col gap-4">
                  <RadioGroup
                    value={runMode}
                    onValueChange={(v) => handleRunModeChange(v as "program" | "template")}
                    className="flex flex-wrap gap-6"
                    disabled={isRunning}
                  >
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="program" id="run-mode-program" />
                      <Label htmlFor="run-mode-program" className="text-slate-200 cursor-pointer">
                        Full program (master + saved tools)
                      </Label>
                    </div>
                    <div className="flex items-center gap-2">
                      <RadioGroupItem value="template" id="run-mode-template" />
                      <Label
                        htmlFor="run-mode-template"
                        className="text-slate-200 cursor-pointer flex items-center gap-1"
                      >
                        <LayoutTemplate className="h-4 w-4" />
                        Tool template + program master
                      </Label>
                    </div>
                  </RadioGroup>

                  <div className="flex flex-wrap items-center gap-4">
                  {/* Program / master host */}
                  <Select 
                    value={selectedProgramId} 
                    onValueChange={handleProgramChange}
                    disabled={programs.length === 0}
                  >
                    <SelectTrigger className="flex-1 bg-slate-950 border-slate-700 text-white">
                      <SelectValue placeholder={
                        programs.length === 0 
                          ? "No programs available" 
                          : runMode === "template"
                            ? "Program (master image)"
                            : "Select program"
                      } />
                    </SelectTrigger>
                    <SelectContent>
                      {programs.map(program => (
                        <SelectItem key={String(program.id)} value={String(program.id)}>
                          {program.name}
                          {runMode === "program"
                            ? ` (${program.config.tools.length} tools)`
                            : " — master host"}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {runMode === "template" && (
                    <Select
                      value={selectedTemplateId}
                      onValueChange={handleTemplateChange}
                      disabled={templatesLoading || templateSummaries.length === 0}
                    >
                      <SelectTrigger className="min-w-[200px] flex-1 bg-slate-950 border-slate-700 text-white">
                        <SelectValue
                          placeholder={
                            templatesLoading
                              ? "Loading templates…"
                              : templateSummaries.length === 0
                                ? "No templates — create in Configure"
                                : "Select tool template"
                          }
                        />
                      </SelectTrigger>
                      <SelectContent>
                        {templateSummaries.map((t) => (
                          <SelectItem key={t.id} value={String(t.id)}>
                            {t.name} ({t.tool_count} tools)
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}

                  {/* Control Buttons */}
                {!isRunning ? (
                  <Button
                    onClick={handleStart}
                    disabled={!canStart}
                    size="lg"
                    className="bg-green-600 hover:bg-green-700 disabled:opacity-50"
                  >
                    <Play className="h-5 w-5 mr-2" />
                    Start
                  </Button>
                ) : (
                  <>
                    <Button
                      onClick={handlePause}
                      size="lg"
                      variant="outline"
                      className="border-yellow-600 text-yellow-600 hover:bg-yellow-600 hover:text-white"
                    >
                      <Pause className="h-5 w-5 mr-2" />
                      {isPaused ? "Resume" : "Pause"}
                    </Button>
                    <Button onClick={handleStop} size="lg" variant="destructive">
                      <Square className="h-5 w-5 mr-2" />
                      Stop
                    </Button>
                  </>
                )}
                
                {/* Manual Trigger Button */}
                <Button 
                  onClick={handleManualTrigger}
                  disabled={!isRunning || isPaused}
                  size="lg"
                  className="bg-orange-600 hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Target className="h-5 w-5 mr-2" />
                  Trigger
                </Button>
                
                <Button onClick={exportResults} variant="outline" size="lg">
                  <Download className="h-5 w-5 mr-2" />
                  Export
                </Button>
                </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Canvas */}
          <Card className="flex-1 bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-white">
                <Camera className="h-5 w-5 text-blue-400" />
                Live Inspection View
              </CardTitle>
            </CardHeader>
            <CardContent className="h-[calc(100%-80px)]">
              <div className="h-full bg-slate-950 rounded-lg flex items-center justify-center relative">
                <canvas
                  ref={canvasRef}
                  width={640}
                  height={480}
                  className="max-w-full max-h-full border border-slate-800 rounded"
                />
                {!currentFrame && !isRunning && (
                  <div className="absolute inset-0 flex items-center justify-center text-slate-500">
                    <div className="text-center">
                      <Camera className="h-16 w-16 mx-auto mb-4 opacity-50" />
                      <p>Camera feed will appear here when inspection starts</p>
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right: Stats & GPIO (40%) */}
        <div className="w-[40%] border-l border-slate-800 overflow-y-auto p-6 space-y-4 bg-slate-950">
          {/* Statistics */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-white">
                <TrendingUp className="h-5 w-5 text-blue-400" />
                Statistics
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-4">
                <div className="p-3 bg-slate-950 rounded border border-slate-800">
                  <div className="text-xs text-slate-400">Total</div>
                  <div className="text-2xl font-bold text-white">{statistics.totalInspected}</div>
                </div>
                <div className="p-3 bg-slate-950 rounded border border-slate-800">
                  <div className="text-xs text-slate-400">Pass Rate</div>
                  <div className="text-2xl font-bold text-green-400">
                    {statistics.passRate.toFixed(1)}%
                  </div>
                </div>
                <div className="p-3 bg-green-950 rounded border border-green-800">
                  <div className="flex items-center gap-2 mb-1">
                    <CheckCircle2 className="h-4 w-4 text-green-400" />
                    <span className="text-xs text-green-400">Passed</span>
                  </div>
                  <div className="text-2xl font-bold text-white">{statistics.passed}</div>
                </div>
                <div className="p-3 bg-red-950 rounded border border-red-800">
                  <div className="flex items-center gap-2 mb-1">
                    <XCircle className="h-4 w-4 text-red-400" />
                    <span className="text-xs text-red-400">Failed</span>
                  </div>
                  <div className="text-2xl font-bold text-white">{statistics.failed}</div>
                </div>
              </div>
              
              <div className="mt-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-400">Avg Processing Time</span>
                  <span className="text-blue-400 font-mono">
                    {statistics.avgProcessingTime.toFixed(2)}ms
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-400">Current Cycle Time</span>
                  <span className="text-blue-400 font-mono">
                    {statistics.currentCycleTime.toFixed(2)}ms
                  </span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-400">Avg Confidence</span>
                  <span className="text-blue-400 font-mono">
                    {statistics.avgConfidence.toFixed(1)}%
                  </span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Tool Results */}
          {currentResult && selectedProgram && (
            <Card className="bg-slate-900 border-slate-800">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-white">
                  <Settings className="h-5 w-5 text-purple-400" />
                  Tool Results
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {getToolsForRun(runMode, selectedProgram, activeToolsForRun).map(
                  (tool) => {
                  const toolResult = findToolForResult(
                    currentResult.toolResults,
                    tool
                  )
                  if (!toolResult) return null
                  return (
                    <div
                      key={tool.id ?? tool.name}
                      className={`p-3 rounded border ${
                        toolResult.status === "OK"
                          ? "bg-green-950 border-green-800"
                          : "bg-red-950 border-red-800"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <div
                            className="w-3 h-3 rounded-full"
                            style={{ backgroundColor: tool.color }}
                          />
                          <span className="text-white font-semibold text-sm">
                            {toolResult.name}
                          </span>
                        </div>
                        <Badge
                          variant={toolResult.status === "OK" ? "default" : "destructive"}
                          className={toolResult.status === "OK" ? "bg-green-600" : ""}
                        >
                          {toolResult.status}
                        </Badge>
                      </div>
                      <div className="space-y-1 text-xs">
                        <div className="flex justify-between">
                          <span className="text-slate-400">Match Rate</span>
                          <span className="text-white font-mono">
                            {toolResult.matching_rate.toFixed(1)}%
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-400">Threshold</span>
                          <span className="text-slate-300 font-mono">
                            {toolResult.threshold}%
                          </span>
                        </div>
                        {/* Progress bar */}
                        <div className="w-full bg-slate-800 rounded-full h-2 mt-2">
                          <div
                            className={`h-2 rounded-full transition-all ${
                              toolResult.status === "OK" ? "bg-green-500" : "bg-red-500"
                            }`}
                            style={{ width: `${Math.min(100, toolResult.matching_rate)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  )
                })}
              </CardContent>
            </Card>
          )}

          {/* GPIO Outputs */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-white">
                <Zap className="h-5 w-5 text-yellow-400" />
                GPIO Outputs
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-2">
                {gpioOutputs.map(gpio => (
                  <div
                    key={gpio.pin}
                    className={`p-2 border rounded transition-all ${
                      gpio.state
                        ? "border-2 shadow-lg"
                        : "border-slate-700 opacity-60"
                    }`}
                    style={{
                      borderColor: gpio.state ? gpio.color : undefined,
                      backgroundColor: gpio.state ? `${gpio.color}20` : "transparent",
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className="w-3 h-3 rounded-full transition-all"
                        style={{
                          backgroundColor: gpio.state ? gpio.color : "#6b7280",
                          boxShadow: gpio.state ? `0 0 8px ${gpio.color}` : "none",
                        }}
                      />
                      <div className="flex-1">
                        <div className="text-xs font-bold text-white">{gpio.pin}</div>
                        <div className="text-xs text-slate-400">{gpio.name}</div>
                      </div>
                      <div className="text-xs text-white font-mono">
                        {gpio.state ? "ON" : "OFF"}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Saved inspection history (database + images) */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="flex items-center gap-2 text-white text-base">
                  <History className="h-5 w-5 text-cyan-400" />
                  Saved history
                </CardTitle>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-slate-400 hover:text-white shrink-0"
                  onClick={() => refreshDbHistory()}
                  disabled={historyLoading || !selectedProgramId}
                  title="Reload from database"
                >
                  <RefreshCw className={`h-4 w-4 ${historyLoading ? "animate-spin" : ""}`} />
                </Button>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                Stored runs with captured images. Click a thumbnail to enlarge.
              </p>
            </CardHeader>
            <CardContent>
              {historyError && (
                <p className="text-xs text-red-400 mb-2">{historyError}</p>
              )}
              <div className="max-h-[320px] overflow-y-auto space-y-2 pr-1">
                {historyLoading && dbInspectionHistory.length === 0 ? (
                  <div className="text-center text-slate-500 py-6 text-sm">Loading history…</div>
                ) : dbInspectionHistory.length === 0 ? (
                  <div className="text-center text-slate-500 py-6 text-sm">
                    No saved inspections yet. Run with the backend connected; images appear after each run.
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-2">
                    {dbInspectionHistory.map(row => {
                      const pid = row.program_id
                      const hasImage = Boolean(row.image_path)
                      const imgUrl = hasImage
                        ? api.getInspectionImageUrl(pid, row.id)
                        : null
                      return (
                        <button
                          key={row.id}
                          type="button"
                          disabled={!hasImage}
                          onClick={() =>
                            hasImage &&
                            setHistoryPreview({
                              programId: pid,
                              resultId: row.id,
                              status: row.overall_status,
                              timestamp: row.timestamp,
                              processingMs: row.processing_time_ms,
                            })
                          }
                          className={`text-left rounded-lg border overflow-hidden transition-opacity ${
                            hasImage
                              ? "border-slate-700 hover:border-cyan-600 focus:outline-none focus:ring-2 focus:ring-cyan-600 cursor-pointer"
                              : "border-slate-800 opacity-60 cursor-not-allowed"
                          }`}
                        >
                          <div className="aspect-video bg-slate-950 flex items-center justify-center relative">
                            {hasImage && imgUrl ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={imgUrl}
                                alt=""
                                className="w-full h-full object-cover"
                                loading="lazy"
                              />
                            ) : (
                              <ImageIcon className="h-8 w-8 text-slate-600" />
                            )}
                            <Badge
                              className={`absolute top-1 right-1 text-[10px] px-1.5 ${
                                row.overall_status === "OK"
                                  ? "bg-green-700"
                                  : "bg-red-700"
                              }`}
                            >
                              {row.overall_status}
                            </Badge>
                          </div>
                          <div className="p-2 bg-slate-950/80">
                            <div className="text-[10px] text-slate-400 font-mono truncate">
                              {new Date(row.timestamp).toLocaleString()}
                            </div>
                            <div className="text-[10px] text-slate-500">
                              {row.processing_time_ms.toFixed(0)} ms
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          <Dialog
            open={historyPreview !== null}
            onOpenChange={open => {
              if (!open) setHistoryPreview(null)
            }}
          >
            <DialogContent className="max-w-4xl bg-slate-950 border-slate-800 text-white">
              {historyPreview && (
                <>
                  <DialogHeader>
                    <DialogTitle className="text-white">
                      Inspection #{historyPreview.resultId} — {historyPreview.status}
                    </DialogTitle>
                    <DialogDescription className="text-slate-400">
                      {new Date(historyPreview.timestamp).toLocaleString()} ·{" "}
                      {historyPreview.processingMs.toFixed(1)} ms
                    </DialogDescription>
                  </DialogHeader>
                  <div className="rounded-lg overflow-hidden border border-slate-800 bg-black">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={api.getInspectionImageUrl(
                        historyPreview.programId,
                        historyPreview.resultId
                      )}
                      alt={`Inspection ${historyPreview.resultId}`}
                      className="w-full max-h-[75vh] object-contain mx-auto"
                    />
                  </div>
                </>
              )}
            </DialogContent>
          </Dialog>

          {/* Recent Results */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-white">
                <Clock className="h-5 w-5 text-orange-400" />
                This session
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 max-h-[300px] overflow-y-auto">
                {recentResults.length === 0 ? (
                  <div className="text-center text-slate-500 py-4 text-sm">
                    No inspections yet
                  </div>
                ) : (
                  recentResults.map(result => (
                    <div
                      key={result.id}
                      className={`p-3 border rounded ${
                        result.status === "OK"
                          ? "border-green-800 bg-green-950"
                          : "border-red-800 bg-red-950"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          {result.status === "OK" ? (
                            <CheckCircle2 className="h-4 w-4 text-green-400" />
                          ) : (
                            <XCircle className="h-4 w-4 text-red-400" />
                          )}
                          <span className="font-bold text-white">{result.status}</span>
                        </div>
                        <span className="text-xs text-slate-400 font-mono">
                          {result.timestamp.toLocaleTimeString()}
                        </span>
                      </div>
                      <div className="text-xs text-slate-300">
                        Confidence: {(result.overallConfidence).toFixed(1)}% | Time:{" "}
                        {result.processingTime.toFixed(1)}ms
                      </div>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
