"use client";

import { useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, Paperclip, Square, Volume2, VolumeX, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Attachment, ModelOption } from "./prompt-composer-utils/types";

export type PromptComposerVariant = "Default" | "Minimal";
export type EditStyleOption = "off" | "balanced" | "pro" | "energetic";

export interface PromptComposerProps {
  placeholder?: string;
  models?: ModelOption[];
  defaultModelId?: string;
  attachments?: Attachment[];
  onRemoveAttachment?: (id: string) => void;
  onAttach?: () => void;
  attachLabel?: string;
  sendLabel?: string;
  inputLabel?: string;
  instructions?: string;
  onInstructionsChange?: (value: string) => void;
  editStyle?: EditStyleOption;
  onEditStyleChange?: (value: EditStyleOption) => void;
  soundFx?: boolean;
  onSoundFxChange?: (value: boolean) => void;
  /** Return false if validation fails, to leave the text in place for correction. */
  onSend?: (text: string, modelId?: string) => void | boolean;
  onStop?: () => void;
  isGenerating?: boolean;
  maxLength?: number;
  showCount?: boolean;
  rows?: number;
  variant?: PromptComposerVariant;
  className?: string;
}

const EDIT_STYLES: { id: EditStyleOption; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "balanced", label: "Balanced" },
  { id: "pro", label: "Pro" },
  { id: "energetic", label: "Energetic" },
];

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function PromptComposer({
  placeholder = "Ask anything…", models = [], defaultModelId, attachments = [],
  onRemoveAttachment, onAttach, attachLabel = "Attach a file", sendLabel = "Send prompt", inputLabel = "Prompt",
  instructions, onInstructionsChange, editStyle, onEditStyleChange, soundFx = true, onSoundFxChange,
  onSend, onStop, isGenerating = false, maxLength = 2000, showCount = true, rows, variant = "Default", className,
}: PromptComposerProps) {
  const [value, setValue] = useState("");
  const [modelId, setModelId] = useState(defaultModelId ?? models[0]?.id);
  const [pickerOpen, setPickerOpen] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const activeModel = models.find((model) => model.id === modelId);
  const canSend = value.trim().length > 0 && !isGenerating;
  const minimal = variant === "Minimal";
  const editsOff = editStyle === "off";

  function submit() {
    if (!canSend) return;
    if (onSend?.(value.trim(), modelId) === false) return;
    setValue("");
    if (textarea.current) textarea.current.style.height = "auto";
  }

  return (
    <form
      onSubmit={(event) => { event.preventDefault(); submit(); }}
      className={cn("composer-shell w-full max-w-[600px] rounded-2xl transition-[border-color,box-shadow] duration-150", className)}
    >
      {attachments.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 px-3 pt-3">
          {attachments.map((attachment) => (
            <li key={attachment.id} className="flex items-center gap-1.5 rounded-lg border border-black/[0.07] bg-black/[0.02] py-1 pl-2 pr-1">
              <Paperclip className="size-3 opacity-60" />
              <span className="max-w-[140px] truncate text-[11.5px] font-medium">{attachment.name}</span>
              <span className="font-mono text-[10px] tabular-nums opacity-60">{formatSize(attachment.size)}</span>
              <button type="button" aria-label={`Remove ${attachment.name}`} onClick={() => onRemoveAttachment?.(attachment.id)} className="grid size-4 place-items-center rounded opacity-60 hover:opacity-100"><X className="size-3" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="px-4 pt-3.5 pb-1">
        <textarea
          ref={textarea} value={value} rows={rows ?? (minimal ? 1 : 2)} maxLength={maxLength}
          placeholder={placeholder} aria-label={inputLabel}
          onChange={(event) => {
            setValue(event.target.value);
            event.target.style.height = "auto";
            event.target.style.height = `${Math.min(event.target.scrollHeight, 132)}px`;
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); submit();
            }
          }}
          className="composer-link-input max-h-[132px] w-full resize-none bg-transparent text-[14.5px] leading-[1.6] outline-none"
        />
      </div>
      {onInstructionsChange && (
        <div className="composer-instructions">
          <label htmlFor="landing-instructions">Special instructions <span>(optional)</span></label>
          <textarea id="landing-instructions" rows={2} maxLength={500} value={instructions ?? ""}
            onChange={(event) => onInstructionsChange(event.target.value)}
            placeholder="e.g. Focus on clutch plays or funny reactions; skip intro chatter" />
          <small>{(instructions ?? "").length}/500 · Guides the AI when selecting moments</small>
        </div>
      )}
      <div className="composer-bar">
        <div className="composer-bar-left">
          {!minimal && (
            <button type="button" aria-label={attachLabel} title={attachLabel} onClick={onAttach} className="composer-icon-btn">
              <Paperclip className="size-3.5" />
            </button>
          )}
          {onEditStyleChange && (
            <div className="composer-edit-strip" role="group" aria-label="Clip editing intensity">
              <span className="composer-strip-lbl">Edit</span>
              <div className="composer-seg">
                {EDIT_STYLES.map((opt) => (
                  <button key={opt.id} type="button" aria-pressed={editStyle === opt.id}
                    className={editStyle === opt.id ? "on" : undefined}
                    onClick={() => onEditStyleChange(opt.id)}>
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {onSoundFxChange && (
            <button type="button" aria-pressed={!editsOff && soundFx} disabled={editsOff}
              title={editsOff ? "Turn editing on to enable sound accents" : "Toggle subtle synthesized sound accents"}
              onClick={() => onSoundFxChange(!soundFx)}
              className={cn("composer-sound-btn", !editsOff && soundFx && "on")}>
              {!editsOff && soundFx ? <Volume2 className="size-3.5" /> : <VolumeX className="size-3.5" />}
              <span>Sound</span>
            </button>
          )}
          {!minimal && models.length > 0 && (
            <div className="relative min-w-0">
              <button type="button" aria-haspopup="listbox" aria-expanded={pickerOpen} onClick={() => setPickerOpen((open) => !open)} className="composer-icon-btn px-2 font-mono text-[11px]">
                <span className="truncate">{activeModel?.name ?? "Model"}</span>
                <ChevronDown className={cn("size-3 shrink-0 transition-transform duration-200", pickerOpen && "rotate-180")} />
              </button>
              {pickerOpen && (
                <ul role="listbox" aria-label="Model" className="absolute bottom-full left-0 z-10 mb-1.5 w-56 rounded-xl border border-black/[0.08] bg-white p-1">
                  {models.map((model) => {
                    const selected = model.id === modelId;
                    return (
                      <li key={model.id}>
                        <button type="button" role="option" aria-selected={selected} disabled={model.disabled} onClick={() => { setModelId(model.id); setPickerOpen(false); }}
                          className={cn("flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors duration-150", model.disabled ? "cursor-not-allowed opacity-40" : "hover:bg-black/[0.04]")}>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[12.5px] font-medium">{model.name}</span>
                            {model.description && <span className="block truncate text-[11px] opacity-60">{model.description}</span>}
                          </span>
                          {selected && <Check className="size-3.5 shrink-0" />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </div>
        <div className="composer-bar-right">
          {!minimal && showCount && <span className="font-mono text-[10.5px] tabular-nums opacity-50">{value.length}/{maxLength}</span>}
          <button type={isGenerating ? "button" : "submit"} onClick={isGenerating ? onStop : undefined}
            aria-label={isGenerating ? "Stop generating" : sendLabel} disabled={!isGenerating && !canSend}
            className="composer-send-btn">
            <span className="relative grid size-3.5 place-items-center">
              <ArrowUp className={cn("absolute size-3.5 transition-[opacity,filter] duration-200", isGenerating ? "opacity-0 blur-[2px]" : "opacity-100 blur-0")} />
              <Square className={cn("absolute size-2.5 fill-current transition-[opacity,filter] duration-200", isGenerating ? "opacity-100 blur-0" : "opacity-0 blur-[2px]")} />
            </span>
          </button>
        </div>
      </div>
    </form>
  );
}
export default PromptComposer;
