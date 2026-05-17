'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import { useToast } from '@/hooks/use-toast';
import { DEFAULT_CAMERA_CAPTURE } from '@/lib/camera-defaults';

import Step2MasterImage from '@/components/wizard/Step2MasterImage';
import Step3ToolConfiguration from '@/components/wizard/Step3ToolConfiguration';
import Step4OutputAssignment from '@/components/wizard/Step4OutputAssignment';

import type { ProgramConfig, ToolConfig, OutputAssignment } from '@/types';
import { normalizeToolRoisForWizardMasterImage } from '@/lib/inspection-engine';

export default function ConfigurePage() {
  const router = useRouter();
  const { toast } = useToast();
  const cam = DEFAULT_CAMERA_CAPTURE;

  // Resolved from URL on mount (avoids need for Suspense wrapper)
  const [programId, setProgramId] = useState<number | null>(null);
  const isEditing = programId !== null;

  // Page-level loading while fetching existing program
  const [isLoading, setIsLoading] = useState(false);

  /** When true, wizard is only for creating a reusable tool template (no full program required). */
  const [templateOnlyMode, setTemplateOnlyMode] = useState(false);

  // Wizard step — when editing jump straight to tool configuration (step 2)
  const [currentStep, setCurrentStep] = useState(1);

  // Trigger settings
  const [triggerType, setTriggerType] = useState<'internal' | 'external'>('internal');
  const [triggerInterval, setTriggerInterval] = useState('1000');
  const [externalDelay, setExternalDelay] = useState('0');

  // Step 1 — Master Image
  const [masterImageRegistered, setMasterImageRegistered] = useState(false);
  const [masterImagePath, setMasterImagePath] = useState<string | null>(null);
  const [masterImageData, setMasterImageData] = useState<string | null>(null);

  // Step 2 — Tool Configuration
  const [configuredTools, setConfiguredTools] = useState<ToolConfig[]>([]);

  // Step 3 — Output Assignment & Name
  const [programName, setProgramName] = useState('');
  const [outputAssignments, setOutputAssignments] = useState<OutputAssignment>({
    OUT1: 'Always ON',
    OUT2: 'OK',
    OUT3: 'NG',
    OUT4: 'Not Used',
    OUT5: 'Not Used',
    OUT6: 'Not Used',
    OUT7: 'Not Used',
    OUT8: 'Not Used',
  });

  // Read ?id= from URL client-side (safe for all Next.js 15 build modes)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('id');
    if (params.get('mode') === 'template') {
      setTemplateOnlyMode(true);
    }
    if (id) {
      const parsed = parseInt(id, 10);
      if (!isNaN(parsed)) {
        setProgramId(parsed);
      }
    }
  }, []);

  // Load program data whenever programId becomes known
  useEffect(() => {
    if (programId !== null) {
      loadProgram(programId);
    }
  }, [programId]);

  const loadProgram = async (id: number) => {
    setIsLoading(true);
    try {
      const program = await api.getProgram(id);

      setProgramName(program.name);

      const cfg = program.config as ProgramConfig;
      if (cfg) {
        setTriggerType(cfg.triggerType ?? 'internal');
        setTriggerInterval(String(cfg.triggerInterval ?? 1000));
        setExternalDelay(String(cfg.triggerDelay ?? 0));
        if (cfg.outputs) setOutputAssignments(cfg.outputs);
        if (cfg.masterImage) setMasterImagePath(cfg.masterImage);
      }

      let toolsToConfigure = (cfg?.tools ?? []) as ToolConfig[];

      // Fetch master image data (base64) so Step3ToolConfiguration can render it
      try {
        const imgRes = await api.getMasterImage(id);
        if (imgRes.image) {
          setMasterImageData(imgRes.image);
          setMasterImageRegistered(true);
          if (toolsToConfigure.length > 0) {
            toolsToConfigure = await normalizeToolRoisForWizardMasterImage(
              imgRes.image,
              toolsToConfigure
            );
          }
        }
      } catch {
        // Master image may not exist yet — that is fine
      }

      if (toolsToConfigure.length > 0) {
        setConfiguredTools(toolsToConfigure);
      } else if (cfg?.tools?.length) {
        setConfiguredTools(cfg.tools);
      }

      // Jump directly to tool-configuration step so the user edits tools on the master image
      setCurrentStep(2);

    } catch (err) {
      toast({
        title: 'Failed to load program',
        description: 'Could not retrieve program data. Please try again.',
        variant: 'destructive',
      });
    } finally {
      setIsLoading(false);
    }
  };

  // ── Wizard navigation ──────────────────────────────────────────────────────

  const steps = [
    { number: 1, title: 'Master Image' },
    { number: 2, title: 'Tool Configuration' },
    { number: 3, title: 'IO Assignment' },
  ];

  const canGoNext = () => {
    switch (currentStep) {
      case 1:
        return (
          masterImageRegistered ||
          (!!masterImageData && masterImageData.length > 32)
        );
      case 2:
        return configuredTools.length > 0;
      default:
        return false;
    }
  };

  const handleToolTemplateSaved = () => {
    setMasterImageData(null);
    setMasterImagePath(null);
    setMasterImageRegistered(false);
    setConfiguredTools([]);
    setCurrentStep(1);
  };

  const handleNext = () => {
    if (currentStep < 3) setCurrentStep(currentStep + 1);
  };

  const handlePrevious = () => {
    if (currentStep > 1) setCurrentStep(currentStep - 1);
  };

  // ── Save / Update ──────────────────────────────────────────────────────────

  const handleSave = async () => {
    const masterPayload =
      masterImageData && masterImageData.length > 32
        ? masterImageData
        : masterImagePath && masterImagePath.length > 32 && !masterImagePath.startsWith('/')
          ? masterImagePath
          : masterImagePath;

    const config: ProgramConfig = {
      triggerType,
      triggerInterval: triggerType === 'internal' ? parseInt(triggerInterval) : 1000,
      triggerDelay: triggerType === 'external' ? parseInt(externalDelay) : 0,
      brightnessMode: cam.brightnessMode,
      focusValue: cam.focusValue,
      exposureTimeUs: cam.exposureTimeUs,
      analogGain: cam.analogGain,
      digitalGain: cam.digitalGain,
      masterImage: masterPayload,
      toolsRoiSpace: 'wizard_640x480',
      tools: configuredTools,
      outputs: outputAssignments,
    };

    const trimmedName = programName.trim();

    if (isEditing && programId !== null) {
      const res = await api.updateProgram(programId, { name: trimmedName, config });
      const tplId = (res.program?.config as ProgramConfig)?.toolTemplateId;
      toast({
        title: 'Program updated!',
        description: tplId
          ? `"${trimmedName}" saved with its own tool template (template #${tplId}). Other programs are unchanged.`
          : `"${trimmedName}" has been saved successfully.`,
      });
    } else {
      const programs = await api.getPrograms(false);
      const existing = programs.find(
        (p) => p.name.trim().toLowerCase() === trimmedName.toLowerCase()
      );
      if (existing) {
        await api.updateProgram(existing.id, { name: trimmedName, config });
        toast({
          title: 'Program updated',
          description: `"${trimmedName}" already existed — your wizard settings were saved to that program.`,
        });
      } else {
        const created = await api.createProgram(trimmedName, config);
        const tplId = (created.config as ProgramConfig)?.toolTemplateId;
        toast({
          title: 'Program created!',
          description: tplId
            ? `"${trimmedName}" is ready with its own tool template (#${tplId}).`
            : `"${trimmedName}" is ready to use.`,
        });
      }
    }

    setTimeout(() => router.push('/'), 1500);
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  const progress = (currentStep / 3) * 100;

  if (isLoading) {
    return (
      <div className="container mx-auto py-8 px-4 max-w-7xl flex flex-col items-center gap-4 mt-24">
        <Loader2 className="h-10 w-10 animate-spin text-muted-foreground" />
        <p className="text-muted-foreground">Loading program configuration…</p>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-8 px-4 max-w-7xl">
      <div className="mb-8">
        <h1 className="text-4xl font-bold">
          {templateOnlyMode && !isEditing
            ? 'Create Tool Configuration Template'
            : isEditing
              ? `Edit Program: ${programName}`
              : 'Configuration Wizard'}
        </h1>
        {templateOnlyMode && !isEditing && (
          <p className="text-sm text-muted-foreground mt-2 max-w-3xl">
            Capture or load a layout image (no registration required), draw tools and thresholds, then save as
            template. Run inspections later from <strong>Run</strong> using &quot;Tool template + program master&quot;
            with any program that has a registered master image.
          </p>
        )}
        {isEditing && (
          <p className="text-sm text-muted-foreground mt-2">
            You are in the configuration wizard on <strong>Step 2: Tool Configuration</strong> (same tools as creating a
            program): live/master canvas, real-time threshold judgment, ROI mask preview, and master strip. Finish with{' '}
            <strong>Step 3: IO Assignment</strong> to save.
          </p>
        )}
      </div>

      {/* Progress */}
      <Card className="p-6 mb-8">
        <div className="space-y-4">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm font-semibold">Step {currentStep} of 3</span>
            <span className="text-sm text-muted-foreground">{steps[currentStep - 1].title}</span>
          </div>

          <Progress value={progress} className="h-2" />

          <div className="grid grid-cols-3 gap-2">
            {steps.map((step) => (
              <div
                key={step.number}
                className={`text-center text-xs ${
                  step.number === currentStep
                    ? 'text-primary font-semibold'
                    : step.number < currentStep
                    ? 'text-green-600'
                    : 'text-muted-foreground'
                }`}
              >
                {step.title}
              </div>
            ))}
          </div>
        </div>
      </Card>

      {/* Step content */}
      <div className="mb-8">
        {currentStep === 1 && (
          <Step2MasterImage
            programId={programId}
            masterImageRegistered={masterImageRegistered}
            setMasterImageRegistered={setMasterImageRegistered}
            masterImagePath={masterImagePath}
            setMasterImagePath={setMasterImagePath}
            masterImageData={masterImageData}
            setMasterImageData={setMasterImageData}
            brightnessMode={cam.brightnessMode}
            focusValue={cam.focusValue}
            exposureTimeUs={cam.exposureTimeUs}
            analogGain={cam.analogGain}
            digitalGain={cam.digitalGain}
          />
        )}

        {currentStep === 2 && (
          <>
            <Card className="mb-6 border-primary/20 bg-primary/5 p-4">
              <p className="text-sm font-semibold text-foreground">Configuration wizard — Step 2: Tool Configuration</p>
              <p className="text-sm text-muted-foreground mt-2">
                Everything below is part of this step: live camera and master views, ROI drawing, processing mask inside the
                region, master reference strip, real-time pass/fail vs the threshold slider (with margin and meters), master vs
                live match percentages, pause/resume camera, threshold-only tuning from the configured-tools list
                (sliders icon). Save a template to store <strong>only tool settings</strong> (no reference image); apply a
                template to any registered master or capture when you configure a program.
              </p>
            </Card>
            <Step3ToolConfiguration
              configuredTools={configuredTools}
              setConfiguredTools={setConfiguredTools}
              masterImageData={masterImageData}
              captureOptions={{
                brightnessMode: cam.brightnessMode,
                focusValue: cam.focusValue,
                exposureTime: cam.exposureTimeUs,
                analogGain: cam.analogGain,
                digitalGain: cam.digitalGain,
              }}
              onToolTemplateSaved={isEditing ? undefined : handleToolTemplateSaved}
              programId={programId}
              programName={programName}
            />
          </>
        )}

        {currentStep === 3 && (
          <Step4OutputAssignment
            programName={programName}
            setProgramName={setProgramName}
            outputAssignments={outputAssignments}
            setOutputAssignments={setOutputAssignments}
            triggerType={triggerType}
            setTriggerType={setTriggerType}
            triggerInterval={triggerInterval}
            setTriggerInterval={setTriggerInterval}
            externalDelay={externalDelay}
            setExternalDelay={setExternalDelay}
            brightnessMode={cam.brightnessMode}
            focusValue={cam.focusValue}
            exposureTimeUs={cam.exposureTimeUs}
            analogGain={cam.analogGain}
            digitalGain={cam.digitalGain}
            masterImageRegistered={masterImageRegistered}
            toolCount={configuredTools.length}
            onSave={handleSave}
          />
        )}
      </div>

      {/* Navigation */}
      <Card className="p-6">
        <div className="flex justify-between">
          <Button
            variant="outline"
            onClick={handlePrevious}
            disabled={currentStep === 1}
          >
            <ChevronLeft className="mr-2 h-4 w-4" />
            Previous
          </Button>

          {currentStep < 3 && (
            <Button onClick={handleNext} disabled={!canGoNext()}>
              Next
              <ChevronRight className="ml-2 h-4 w-4" />
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}
