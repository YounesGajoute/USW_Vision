'use client';

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { CheckCircle2, AlertCircle, Loader2, Save } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import type { OutputAssignment, OutputCondition, OutputName } from '@/types';
import { OUTPUT_CONDITIONS } from '@/types';

interface Step4Props {
  programName: string;
  setProgramName: (name: string) => void;
  outputAssignments: OutputAssignment;
  setOutputAssignments: (assignments: OutputAssignment) => void;
  triggerType: 'internal' | 'external';
  setTriggerType: (type: 'internal' | 'external') => void;
  triggerInterval: string;
  setTriggerInterval: (value: string) => void;
  externalDelay: string;
  setExternalDelay: (value: string) => void;
  brightnessMode: string;
  focusValue: number;
  exposureTimeUs: number;
  analogGain: number;
  digitalGain: number;
  masterImageRegistered: boolean;
  toolCount: number;
  onSave: () => Promise<void>;
}

export default function Step4OutputAssignment({
  programName,
  setProgramName,
  outputAssignments,
  setOutputAssignments,
  triggerType,
  setTriggerType,
  triggerInterval,
  setTriggerInterval,
  externalDelay,
  setExternalDelay,
  brightnessMode,
  focusValue,
  exposureTimeUs,
  analogGain,
  digitalGain,
  masterImageRegistered,
  toolCount,
  onSave,
}: Step4Props) {
  const [isSaving, setIsSaving] = useState(false);
  const { toast } = useToast();

  const handleOutputChange = (output: OutputName, condition: OutputCondition) => {
    setOutputAssignments({
      ...outputAssignments,
      [output]: condition,
    });
  };

  const handleSave = async () => {
    // Validation
    if (!programName.trim()) {
      toast({
        title: "Validation Error",
        description: "Program name is required",
        variant: "destructive",
      });
      return;
    }

    if (!masterImageRegistered) {
      toast({
        title: "Validation Error",
        description: "Master image must be registered",
        variant: "destructive",
      });
      return;
    }

    if (toolCount === 0) {
      toast({
        title: "Validation Error",
        description: "At least one inspection tool must be configured",
        variant: "destructive",
      });
      return;
    }

    setIsSaving(true);
    
    try {
      await onSave();
      
      toast({
        title: "Program Saved!",
        description: `"${programName}" has been created successfully`,
      });
    } catch (error) {
      console.error('Save failed:', error);
      toast({
        title: "Save Failed",
        description: error instanceof Error ? error.message : "Failed to save program",
        variant: "destructive",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const getValidationStatus = () => {
    const checks = [
      { label: 'Program name', valid: programName.trim().length > 0 },
      { label: 'Master image', valid: masterImageRegistered },
      { label: 'Inspection tools', valid: toolCount > 0 },
    ];

    const allValid = checks.every(c => c.valid);
    
    return { checks, allValid };
  };

  const { checks, allValid } = getValidationStatus();

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-3xl font-bold">Step 3: IO Assignment Configuration & Save</h2>
      </div>

      {/* IO Assignment Configuration (Trigger Configuration) */}
      <Card>
        <CardHeader>
          <CardTitle>IO Assignment Configuration</CardTitle>
          <CardDescription>
            Configure input trigger mode and timing settings
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Trigger Type Radio Group */}
          <div className="space-y-3">
            <Label className="text-base font-semibold">Trigger Type</Label>
            <RadioGroup 
              value={triggerType} 
              onValueChange={(value) => setTriggerType(value as 'internal' | 'external')}
            >
              <div className="flex items-start space-x-3 p-4 border rounded-lg hover:bg-accent/50 transition-colors">
                <RadioGroupItem value="internal" id="internal" className="mt-1" />
                <div className="flex-1">
                  <Label htmlFor="internal" className="text-base font-medium cursor-pointer">
                    Internal (Timer-based)
                  </Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    Automatically trigger inspections at regular time intervals
                  </p>
                </div>
              </div>
              
              <div className="flex items-start space-x-3 p-4 border rounded-lg hover:bg-accent/50 transition-colors">
                <RadioGroupItem value="external" id="external" className="mt-1" />
                <div className="flex-1">
                  <Label htmlFor="external" className="text-base font-medium cursor-pointer">
                    External (GPIO trigger)
                  </Label>
                  <p className="text-sm text-muted-foreground mt-1">
                    Trigger inspections from external GPIO input signal
                  </p>
                </div>
              </div>
            </RadioGroup>
          </div>

          {/* Conditional Inputs */}
          {triggerType === 'internal' ? (
            <div className="space-y-2">
              <Label htmlFor="interval" className="text-base font-semibold">
                Interval (milliseconds)
              </Label>
              <Input
                id="interval"
                type="number"
                min={1}
                max={10000}
                value={triggerInterval}
                onChange={(e) => setTriggerInterval(e.target.value)}
                placeholder="1000"
                className="max-w-xs"
              />
              <p className="text-sm text-muted-foreground">
                Valid range: 1-10,000 ms (1 ms to 10 seconds)
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="delay" className="text-base font-semibold">
                Delay (milliseconds)
              </Label>
              <Input
                id="delay"
                type="number"
                min={0}
                max={1000}
                value={externalDelay}
                onChange={(e) => setExternalDelay(e.target.value)}
                placeholder="0"
                className="max-w-xs"
              />
              <p className="text-sm text-muted-foreground">
                Valid range: 0-1,000 ms (delay after GPIO trigger signal)
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Program Name */}
      <Card>
        <CardHeader>
          <CardTitle>Program Name</CardTitle>
          <CardDescription>Enter a unique name for this inspection program</CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            placeholder="e.g., Widget Assembly Inspection"
            value={programName}
            onChange={(e) => setProgramName(e.target.value)}
            className="text-lg"
          />
        </CardContent>
      </Card>

      {/* Output Assignments */}
      <Card>
        <CardHeader>
          <CardTitle>GPIO Output Assignments</CardTitle>
          <CardDescription>
            Configure what each output does based on inspection results
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Fixed Outputs */}
          <div className="space-y-3">
            <h3 className="font-semibold text-sm text-muted-foreground uppercase">
              System Outputs (Fixed)
            </h3>
            
            {[1, 2, 3].map((num) => {
              const outputName = `OUT${num}` as OutputName;
              const labels = {
                OUT1: 'BUSY Signal',
                OUT2: 'OK Signal',
                OUT3: 'NG Signal',
              } as const;
              
              return (
                <div key={num} className="flex items-center justify-between p-3 bg-accent/30 rounded-lg">
                  <div>
                    <Label className="font-semibold">{outputName}</Label>
                    <p className="text-sm text-muted-foreground">{labels[outputName as keyof typeof labels]}</p>
                  </div>
                  <Badge variant="secondary">Fixed</Badge>
                </div>
              );
            })}
          </div>

          {/* Configurable Outputs */}
          <div className="space-y-3">
            <h3 className="font-semibold text-sm text-muted-foreground uppercase">
              Configurable Outputs
            </h3>
            
            {[4, 5, 6, 7, 8].map((num) => {
              const outputName = `OUT${num}` as OutputName;
              
              return (
                <div key={num} className="flex items-center justify-between p-3 border rounded-lg">
                  <Label className="font-semibold flex-shrink-0 w-16">{outputName}</Label>
                  <Select
                    value={outputAssignments[outputName]}
                    onValueChange={(value) => handleOutputChange(outputName, value as OutputCondition)}
                  >
                    <SelectTrigger className="flex-1 max-w-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OUTPUT_CONDITIONS.map((condition) => (
                        <SelectItem key={condition} value={condition}>
                          {condition}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Configuration Summary */}
      <Card>
        <CardHeader>
          <CardTitle>Configuration Summary</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-sm text-muted-foreground">Trigger Type</p>
              <p className="font-semibold capitalize">{triggerType}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Brightness Mode</p>
              <p className="font-semibold capitalize">{brightnessMode}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Focus Value</p>
              <p className="font-semibold">{focusValue}%</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Exposure</p>
              <p className="font-semibold">{(exposureTimeUs / 1000).toFixed(1)} ms ({exposureTimeUs} µs)</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Analog / digital gain</p>
              <p className="font-semibold">
                {analogGain.toFixed(2)}× / {digitalGain.toFixed(2)}×
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Configured Tools</p>
              <p className="font-semibold">{toolCount} tools</p>
            </div>
          </div>

          {/* Validation Checklist */}
          <div className="space-y-2 pt-4 border-t">
            <h4 className="font-semibold text-sm">Configuration Status</h4>
            {checks.map((check) => (
              <div key={check.label} className="flex items-center gap-2">
                {check.valid ? (
                  <CheckCircle2 className="h-4 w-4 text-green-600" />
                ) : (
                  <AlertCircle className="h-4 w-4 text-yellow-600" />
                )}
                <span className="text-sm">{check.label}</span>
                {check.valid && (
                  <Badge variant="outline" className="text-xs">Complete</Badge>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Save Button */}
      <Card className={allValid ? "bg-primary/5 border-primary/20" : "bg-yellow-50 dark:bg-yellow-950/20 border-yellow-200 dark:border-yellow-800"}>
        <CardContent className="pt-6">
          {allValid ? (
            <div>
              <p className="text-sm mb-4">
                Configuration is complete and valid. Click below to save your inspection program.
              </p>
              <Button
                onClick={handleSave}
                disabled={isSaving}
                size="lg"
                className="w-full"
              >
                {isSaving ? (
                  <>
                    <Loader2 className="mr-2 h-5 w-5 animate-spin" />
                    Saving Program...
                  </>
                ) : (
                  <>
                    <Save className="mr-2 h-5 w-5" />
                    Save Inspection Program
                  </>
                )}
              </Button>
            </div>
          ) : (
            <div>
              <p className="text-sm text-yellow-900 dark:text-yellow-100 mb-2">
                Please complete all required steps before saving:
              </p>
              <ul className="text-sm text-yellow-800 dark:text-yellow-200 list-disc list-inside">
                {checks.filter(c => !c.valid).map(c => (
                  <li key={c.label}>{c.label}</li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

