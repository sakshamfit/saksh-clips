"use client";

import * as React from "react";

/** Doodle Crew Footer from sakshamfit/orion-gkp, adapted for Anhy Clips. */
export interface DoodleCrewFooterProps {
  wordmark?: string;
  brand?: string;
  blurb?: string;
  tagline?: string;
  copyright?: string;
  columns?: { title: string; links: { label: string; href: string }[] }[];
  actions?: { label: string; href: string }[];
  socials?: { label: string; href: string }[];
}

// Adapted from sakshamfit/orion-gkp for Anhy Clips. Keep all destinations on this site.
const defaults = {
  columns: [
    { title: "Product", links: [
      { label: "How it works", href: "/#how" }, { label: "Features", href: "/#features" },
      { label: "Live clipping", href: "/#live" }, { label: "Open source", href: "/#open" },
    ] },
    { title: "Get started", links: [
      { label: "Create an account", href: "/signup" }, { label: "Sign in", href: "/login" },
      { label: "Reset password", href: "/reset" }, { label: "FAQ", href: "/#faq" },
    ] },
    { title: "Legal", links: [
      { label: "Terms of Service", href: "/terms" }, { label: "Privacy Policy", href: "/privacy" },
      { label: "Acceptable Use", href: "/acceptable-use" },
    ] },
  ],
  actions: [{ label: "How it works", href: "/#how" }, { label: "Start clipping ↗", href: "/signup" }],
  socials: [{ label: "Source code ↗", href: "https://github.com/sakshamfit/saksh-clips" }],
};

function Friend({ name, line, kind }: { name: string; line: string; kind: "cat" | "bird" | "ghost" }) {
  const [speaking, setSpeaking] = React.useState(false);
  const [gaze, setGaze] = React.useState({ x: 0, y: 0 });
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const say = () => { setSpeaking(true); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setSpeaking(false), 2500); };
  return <div className={`orion-friend friend-${kind}`}>
    {speaking && <span className="orion-bubble" role="status">{line}</span>}
    <button type="button" aria-label={`${name}: ${line}`} onClick={say} onPointerMove={(event) => {
      const box = event.currentTarget.getBoundingClientRect();
      setGaze({ x: Math.max(-2, Math.min(2, (event.clientX - box.left - box.width / 2) / 14)), y: Math.max(-2, Math.min(2, (event.clientY - box.top - box.height / 2) / 14)) });
    }} onPointerLeave={() => setGaze({ x: 0, y: 0 })}>
      <svg viewBox="0 0 100 112" aria-hidden="true">
        {kind === "cat" ? <><path d="M19 47 18 11 39 28 Q50 23 62 28 L82 11 81 47 Q91 59 87 77 Q83 98 50 98 Q17 98 13 77 Q9 59 19 47Z"/><path d="M28 44 30 26 40 34 M72 44 70 26 60 34" fill="none"/></> : kind === "bird" ? <><path d="M17 67 Q12 28 50 25 Q87 28 83 67 Q80 96 50 98 Q20 96 17 67Z"/><path d="M39 27 50 7 61 27Z" fill="#e9a274"/><path d="M50 64 61 69 50 74Z" fill="#e9a274"/></> : <><path d="M17 94 V51 Q17 17 50 17 Q83 17 83 51 V94 L72 85 61 94 50 85 39 94 28 85Z"/><path d="M22 55 Q9 46 10 65 M78 55 Q91 46 90 65" fill="none"/></>}
        <g transform={`translate(${gaze.x} ${gaze.y})`}><circle cx="38" cy="58" r="4" fill="#344a38" stroke="none"/><circle cx="62" cy="58" r="4" fill="#344a38" stroke="none"/></g><path d="M43 75 Q50 81 57 75" fill="none"/>
      </svg>
    </button>
  </div>;
}

function SocialIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 6v-3.2a3.1 3.1 0 0 0-.9-2.4c3-.4 6.1-1.5 6.1-6.6a5.1 5.1 0 0 0-1.4-3.5 4.7 4.7 0 0 0-.1-3.5S17.6 2 15 4a13 13 0 0 0-6 0C6.4 2 5.3 2.8 5.3 2.8a4.7 4.7 0 0 0-.1 3.5 5.1 5.1 0 0 0-1.4 3.5c0 5.1 3.1 6.2 6.1 6.6a3.1 3.1 0 0 0-.9 2.4V22" />
  </svg>;
}

export default function DoodleCrewFooter({ wordmark = "ANHY", brand = "Anhy Clips", blurb = "Find the moments that matter. Turn your streams and videos into clips worth sharing.", tagline = "Make every moment count.", copyright = `© ${new Date().getFullYear()} Anhy Clips`, columns = defaults.columns, actions = defaults.actions, socials = defaults.socials }: DoodleCrewFooterProps) {
  const root = React.useRef<HTMLElement>(null);
  const [visible, setVisible] = React.useState(false);
  React.useEffect(() => {
    const el = root.current;
    if (!el) return;
    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setVisible(true); return; }
    const observer = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { setVisible(true); observer.disconnect(); } }, { threshold: 0.08 });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return <footer id="footer" className={`orion-doodle-footer ${visible ? "is-visible" : ""}`} ref={root}>
    <div className="orion-footer-top"><div><div className="orion-footer-brand"><img className="orion-footer-logo" src="/brand/logo.svg" alt={brand} /></div><p>{blurb}</p></div><nav className="orion-footer-columns" aria-label="Footer navigation">{columns.map(column => <div key={column.title}><h3>{column.title}</h3>{column.links.map(link => <a key={link.label} href={link.href} target={link.href.startsWith("https://") ? "_blank" : undefined} rel={link.href.startsWith("https://") ? "noopener noreferrer" : undefined}>{link.label}</a>)}</div>)}</nav></div>
    <div className="orion-doodle-stage"><span className="orion-doodle doodle-one" aria-hidden="true">✳</span><span className="orion-doodle doodle-two" aria-hidden="true">✦</span><span className="orion-doodle doodle-three" aria-hidden="true">♡</span><div className="orion-word" role="img" aria-label={wordmark}>{Array.from(wordmark.toUpperCase()).map((letter, index) => <span aria-hidden="true" key={index} style={{ animationDelay: `${index * 90}ms` }}>{letter}</span>)}</div><Friend kind="cat" name="Clip cat" line="Find your next highlight!"/><Friend kind="bird" name="Clip bird" line="Clip it while it is live!"/><Friend kind="ghost" name="Clip friend" line="See you in the studio!"/><div className="orion-ground"/></div>
    <div className="orion-footer-cta"><p>{tagline}</p><div>{actions.map((action, index) => <a key={action.label} className={index ? "orion-pill solid" : "orion-pill"} href={action.href} target={action.href.startsWith("https://") ? "_blank" : undefined} rel={action.href.startsWith("https://") ? "noopener noreferrer" : undefined}>{action.label}</a>)}</div></div>
    <div className="orion-footer-bottom"><span>{copyright} · Free and open source.</span><div>{socials.map(social => <a key={social.label} href={social.href} target="_blank" rel="noopener noreferrer" className="footer-social-icon" aria-label={social.label.replace(" ↗", "")}><SocialIcon /><span className="footer-social-text">{social.label}</span></a>)}</div><a href="#top">Back to top ↑</a></div>
  </footer>;
}
