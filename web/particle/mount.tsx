import { useState } from "react";
import { createRoot } from "react-dom/client";
import DemoOne from "./demo";
import PromptComposer from "@/components/ui/prompt-composer";
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

function LandingComposer() {
  const [error, setError] = useState("");
  const [instructions, setInstructions] = useState("");
  return (
    <div className="landing-composer">
      <PromptComposer
        placeholder="Paste a YouTube, Twitch or Kick link…"
        maxLength={500}
        showCount={false}
        rows={1}
        attachLabel="Upload a video in the app"
        inputLabel="Video or stream link"
        sendLabel="Use this video link"
        instructions={instructions}
        onInstructionsChange={setInstructions}
        onAttach={goToWorkspace}
        onSend={(text) => {
          if (!supportedLink(text)) {
            setError("Enter a YouTube, Twitch or Kick link to get started.");
            return false;
          }
          setError("");
          try {
            sessionStorage.setItem("anhy_link", text);
            if (instructions.trim()) sessionStorage.setItem("anhy_instructions", instructions.trim());
            else sessionStorage.removeItem("anhy_instructions");
          } catch { /* storage may be disabled */ }
          goToWorkspace();
        }}
      />
      <p className="composer-hint" role={error ? "alert" : undefined}>
        {error || "Paste a link to start clipping · Use the paperclip to upload in the app"}
      </p>
    </div>
  );
}

const composer = document.getElementById("landing-composer-root");
if (composer) createRoot(composer).render(<LandingComposer />);

const footer = document.getElementById("landing-footer-root");
if (footer) createRoot(footer).render(<DoodleCrewFooter />);
