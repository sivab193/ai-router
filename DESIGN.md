---
name: ai-router Control Plane
colors: { ground: "#0b0e11", topbar: "#0e1215", surface: "#11161a", surface-raised: "#171d22", code-well: "#090c0e", seam: "#2b343b", seam-strong: "#46515a", ink: "#eef3f5", code-ink: "#d7e9f0", muted: "#9aa8b2", faint: "#74838d", placeholder: "#788893", online: "#53d49b", online-hover: "#75e5b4", primary-ink: "#07120e", warning: "#f2bc62", danger: "#ff746d", danger-seam: "#71403d", signal: "#61bee4", focus: "#a6ddf5" }
typography:
  display-auth: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "clamp(34px, 6vw, 68px)", fontWeight: 700, lineHeight: 0.98, letterSpacing: "-0.04em" }
  display: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "clamp(30px, 4vw, 52px)", fontWeight: 700, lineHeight: 1.02, letterSpacing: "-0.035em" }
  headline: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "22px", fontWeight: 700, lineHeight: 1.5, letterSpacing: "-0.025em" }
  title: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "20px", fontWeight: 700, lineHeight: 1.5, letterSpacing: "-0.02em" }
  body: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "15px", fontWeight: 400, lineHeight: 1.5, letterSpacing: "normal" }
  label: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "11px", fontWeight: 400, lineHeight: 1.5, letterSpacing: "0.08em" }
  mono-data: { fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace', fontSize: "13px", fontWeight: 400, lineHeight: 1.5, letterSpacing: "normal" }
  action: { fontFamily: '"Space Grotesk", "Segoe UI", sans-serif', fontSize: "15px", fontWeight: 700, lineHeight: 1.5, letterSpacing: "normal" }
rounded: { signal-pulse: "3px", control: "8px", panel: "12px", lamp: "50%" }
spacing: { compact: "8px", control-gap: "10px", inset: "14px", rhythm: "20px", section-gap: "24px", frame: "32px", frame-top: "36px", frame-bottom: "64px" }
components:
  button-primary: { backgroundColor: "{colors.online}", textColor: "{colors.primary-ink}", typography: "{typography.action}", rounded: "{rounded.control}", padding: "9px 13px" }
  button-utility: { backgroundColor: "transparent", textColor: "{colors.ink}", typography: "{typography.body}", rounded: "{rounded.control}", padding: "9px 13px" }
  button-destructive: { backgroundColor: "transparent", textColor: "{colors.danger}", typography: "{typography.body}", rounded: "{rounded.control}", padding: "9px 13px" }
  field: { backgroundColor: "{colors.surface}", textColor: "{colors.ink}", typography: "{typography.body}", rounded: "{rounded.control}", padding: "10px 12px" }
  command-well: { backgroundColor: "{colors.code-well}", textColor: "{colors.code-ink}", typography: "{typography.mono-data}", rounded: "{rounded.control}", padding: "14px" }
---

Full: .impeccable/design-full.md
