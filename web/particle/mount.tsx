import { useState } from "react";
import { createRoot } from "react-dom/client";
import DemoOne from "./demo";
import PromptComposer, { type EditStyleOption } from "@/components/ui/prompt-composer";
import DoodleCrewFooter from "@/components/ui/doodle-crew-footer";

const mount = document.getElementById("particle-title-root");
if (mount) createRoot(mount).render(<DemoOne />);

function supportedLink(input: string) {
  try {
    const url = new URL(input);
    if (url.protocol !== "https:" && url.protocol !== "http:") return false;
    const host = url.hostname.toLowerCase();
    return ["youtube.com", "youtu.be", "twitch.tv", "kick.com"].some(
      (domain) => host === domain || host.endsWith(`.${domain}`),
    );
  } catch {
    return false;
  }
}

function goToWorkspace() {
  fetch("/api/auth/me")
    .then((response) => response.json())
    .then((data) => { location.href = data.user ? "/app" : "/signup"; })
    .catch(() => { location.href = "/signup"; });
}

const EDIT_HINTS: Record<EditStyleOption, string> = {
  off: "Off: keeps framing, color, and audio untouched.",
  balanced: "Balanced: studio voice mastering, film clarity grade, subtle dolly motion & clean punch-ins.",
  pro: "Pro: dead-air jump-cuts, multi-cam framing shifts, snappy punch-ins, climax impact hit, film grade & studio voice.",
  energetic: "Energetic: tightest jump-cut pacing, 1.38x snap zooms, climax micro-shake & full cinematic SFX.",
};

function LandingComposer() {
  const [error, setError] = useState("");
  const [instructions, setInstructions] = useState("");
  const [editStyle, setEditStyle] = useState<EditStyleOption>("pro");
  const [soundFx, setSoundFx] = useState(true);

  function savePrefs() {
    try {
      sessionStorage.setItem("anhy_edit_style", editStyle);
      sessionStorage.setItem("anhy_sound_fx", soundFx ? "1" : "0");
      if (instructions.trim()) sessionStorage.setItem("anhy_instructions", instructions.trim());
      else sessionStorage.removeItem("anhy_instructions");
    } catch { /* storage may be disabled */ }
  }

  return (
    <div className="landing-composer">
      <PromptComposer
        placeholder="Paste a YouTube, Twitch or Kick link…"
        maxLength={500}
        showCount={false}
        rows={1}
        attachLabel="Upload a video or pick a local file in the app"
        inputLabel="Video or stream link"
        sendLabel="Start clipping this video"
        instructions={instructions}
        onInstructionsChange={setInstructions}
        editStyle={editStyle}
        onEditStyleChange={setEditStyle}
        soundFx={soundFx}
        onSoundFxChange={setSoundFx}
        onAttach={() => { savePrefs(); goToWorkspace(); }}
        onSend={(text) => {
          if (!supportedLink(text)) {
            setError("Enter a YouTube, Twitch or Kick link to get started.");
            return false;
          }
          setError("");
          try { sessionStorage.setItem("anhy_link", text); } catch { /* ignore */ }
          savePrefs();
          goToWorkspace();
        }}
      />
      <p className={`composer-hint${error ? " is-error" : ""}`} role={error ? "alert" : undefined}>
        {error || EDIT_HINTS[editStyle]}
      </p>
    </div>
  );
}

const composer = document.getElementById("landing-composer-root");
if (composer) createRoot(composer).render(<LandingComposer />);

const footer = document.getElementById("landing-footer-root");
if (footer) createRoot(footer).render(<DoodleCrewFooter />);
