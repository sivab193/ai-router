---
name: ai-router Control Plane
description: A severe, legible switching console for a live inference fleet.
colors:
  ground: "#0b0e11"
  topbar: "#0e1215"
  surface: "#11161a"
  surface-raised: "#171d22"
  code-well: "#090c0e"
  seam: "#2b343b"
  seam-strong: "#46515a"
  ink: "#eef3f5"
  code-ink: "#d7e9f0"
  muted: "#9aa8b2"
  faint: "#74838d"
  placeholder: "#788893"
  online: "#53d49b"
  online-hover: "#75e5b4"
  primary-ink: "#07120e"
  warning: "#f2bc62"
  danger: "#ff746d"
  danger-seam: "#71403d"
  signal: "#61bee4"
  focus: "#a6ddf5"
typography:
  display-auth:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "clamp(34px, 6vw, 68px)"
    fontWeight: 700
    lineHeight: 0.98
    letterSpacing: "-0.04em"
  display:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "clamp(30px, 4vw, 52px)"
    fontWeight: 700
    lineHeight: 1.02
    letterSpacing: "-0.035em"
  headline:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-0.025em"
  title:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "-0.02em"
  body:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  label:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0.08em"
  mono-data:
    fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  action:
    fontFamily: '"Space Grotesk", "Segoe UI", sans-serif'
    fontSize: "15px"
    fontWeight: 700
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  signal-pulse: "3px"
  control: "8px"
  panel: "12px"
  lamp: "50%"
spacing:
  compact: "8px"
  control-gap: "10px"
  inset: "14px"
  rhythm: "20px"
  section-gap: "24px"
  frame: "32px"
  frame-top: "36px"
  frame-bottom: "64px"
components:
  button-primary:
    backgroundColor: "{colors.online}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-primary-hover:
    backgroundColor: "{colors.online-hover}"
    textColor: "{colors.primary-ink}"
    typography: "{typography.action}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-utility:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  button-destructive:
    backgroundColor: "transparent"
    textColor: "{colors.danger}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "9px 13px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  command-well:
    backgroundColor: "{colors.code-well}"
    textColor: "{colors.code-ink}"
    typography: "{typography.mono-data}"
    rounded: "{rounded.control}"
    padding: "14px"
---

# Design System: ai-router Control Plane

## Overview

**Creative North Star: "The Live Backplane"**

**FORM Seed:** `digital-design-canon-dark-first-developer-console`

The interface behaves like a persistent switching console: a severe but legible graphite field whose structure is exposed through hard seams, labelled channels, and an always-readable data hierarchy. The backplane is continuous rather than subdivided into a decorative card grid, so the operator reads the fleet as one connected system.

Operational truth is the only source of color or motion. Live state, warnings, failures, aliases, focus, and the restrained refresh pulse carry signal; everything else stays neutral. The result is dense enough for infrastructure work but calm enough to leave unknown values visibly unknown. Glass, gradients, ornamental imagery, and decorative effects are outside this visual language.

**Key Characteristics:**

- Continuous dark backplane with hard structural seams.
- Operational color only, assigned by state and action.
- Space Grotesk for interface hierarchy; monospaced type for machine identity and telemetry.
- Full-width runner channels instead of floating cards.
- One restrained signal-rail pulse after a successful refresh.
- Responsive reflow without horizontal scrolling or loss of the primary machine-connect action.

## Colors

The palette is a cool graphite neutral system with narrow mint, amber, coral, and cyan signals reserved for operational meaning.

### Primary

- **Routable Mint** (`online`): Marks connected, routable state and the primary machine-enrollment action.
- **Routable Mint Hover** (`online-hover`): The sole brighter primary interaction state.
- **Primary Ink** (`primary-ink`): Keeps text dense and legible on the mint action surface.

### Secondary

- **Relay Cyan** (`signal`): Identifies model aliases, text selection, and the refresh pulse moving down the signal rail.
- **Focus Ice** (`focus`): Provides the universal keyboard-focus outline without competing with status color.

### Tertiary

- **Attention Amber** (`warning`): Signals connecting state or gateway synchronization that needs attention.
- **Revocation Coral** (`danger`): Signals revoked, failed, or destructive action state.
- **Revocation Seam** (`danger-seam`): Contains destructive controls without giving them a filled, primary-action silhouette.

### Neutral

- **Console Ground** (`ground`): The page canvas and deepest persistent field.
- **Topbar Plane** (`topbar`): A subtle plane shift for the global session bar.
- **Control Surface** (`surface`): The input fill and standard inset working surface.
- **Raised Interaction Plane** (`surface-raised`): Appears only on neutral-control hover.
- **Code Well** (`code-well`): The deepest inset used for one-time install commands.
- **Structural Seam** (`seam`): Divides regions and channels without creating boxes.
- **Strong Structural Seam** (`seam-strong`): Defines inputs, buttons, the signal rail, and channel origins.
- **Console Ink** (`ink`): Primary copy and headings.
- **Code Ink** (`code-ink`): Slightly softer high-contrast text inside command output.
- **Muted Copy** (`muted`): Explanatory text, field labels, and secondary status prose.
- **Faint Telemetry** (`faint`): Timestamps, metadata, measurement labels, and offline lamps.
- **Placeholder Telemetry** (`placeholder`): Input placeholder text only.

### Named Rules

**The Operational Color Rule.** Color must report live state, interaction, or routing identity; it is never atmospheric decoration.

**The Fail-Closed Rule.** Offline state returns to neutral and preserves last-seen context; it never borrows the mint reserved for routable compute.

## Typography

**Display Font:** Space Grotesk (with Segoe UI and sans-serif fallbacks)
**Body Font:** Space Grotesk (with Segoe UI and sans-serif fallbacks)
**Label/Mono Font:** SFMono-Regular (with Consolas, Liberation Mono, and monospace fallbacks)

**Character:** The locally bundled variable Space Grotesk gives the console a direct, engineered voice without becoming sterile. The system monospace stack separates observed identifiers, timestamps, commands, and measured values from explanatory interface copy.

### Hierarchy

- **Authentication Display:** The largest, tightest statement, used only before the console is unlocked.
- **Display:** Heavy, tightly tracked, and compact; reserved for the unlocked workspace thesis.
- **Headline:** Strong section-level language for the enrollment work surface.
- **Title:** Runner identity and empty-state emphasis.
- **Body:** The default control and prose voice, with explanatory lines generally held near 42–64 characters.
- **Label:** Small uppercase measurement labels whose tracking creates scannable telemetry columns.
- **Mono Data:** Aliases, timestamps, commands, numeric readings, and reported machine values; use tabular numerals where values update in place.
- **Action:** The bold primary-action label used only on mint controls.

### Named Rules

**The Data Voice Rule.** Human explanation uses Space Grotesk; machine identity, observation, and command output use the mono stack.

**The Honest Label Rule.** Unknown and unreported values receive the same typographic dignity as known values; never fade them into invisibility.

## Layout

The page is capped at a 1500px work area with a 32px desktop frame, a 36px top inset, and a 64px bottom inset. The overview separates the thesis from aggregate fleet state, then one workplane divides fleet and enrollment at a 2.1fr to 0.9fr ratio. The fleet begins on a vertical signal rail; full-width runner channels connect to it and subdivide only with horizontal seams.

At 900px and below, the workplane becomes one ordered column, the right divider becomes a top seam, the page frame contracts to 20px, and runner measurements become two columns. At 560px and below, the topbar compacts, the secondary brand line and manual refresh control disappear, runner headings and footers stack, and **Connect machine** appears in the first viewport as the primary action linking to enrollment. Measurements remain two labelled columns with no horizontal scrolling.

**The Backplane Continuity Rule.** Preserve the signal rail, connected channel marks, and full-width rows; do not break fleet state into independent cards.

## Elevation & Depth

The system is flat and structurally layered. Depth comes from the progression of graphite planes and 1px seams, not from floating surfaces. There are no panel shadows, backdrop blur, glass effects, or gradients; the only shadow is a compact status-lamp anchor so the tiny live-state mark remains distinct against the backplane.

### Shadow Vocabulary

- **Status Lamp Anchor** (`0 3px 10px rgba(0,0,0,.35)`): Used only beneath the 9px runner-state lamp.

### Named Rules

**The Seam-over-Shadow Rule.** Establish hierarchy with tonal planes and 1px structural seams; never lift an ordinary container into a card.

## Shapes

The shell, workplane, and runner channels are square and continuous. The 8px control radius belongs to buttons, fields, and the command well; the small 3px pulse radius softens only the moving rail signal, while status lamps are fully circular. The declared 12px panel radius is available as a system primitive but is not applied to the current backplane composition.

**The Hard Plane Rule.** Round controls for touch clarity, not containers for decoration; structural regions keep hard seams and square silhouettes.

## Components

### Buttons

- **Shape:** Compact control silhouette with an 8px radius and 1px boundary.
- **Primary:** Routable mint with dark primary ink, bold label weight, and 9px by 13px padding.
- **Hover / Focus:** Primary brightens to its dedicated hover tone; neutral controls move to the raised interaction plane. All variants use a 120ms ease-out color transition and the universal 2px focus outline offset by 3px.
- **Utility:** Transparent with a strong neutral seam; hover increases both fill and border contrast.
- **Destructive:** Transparent coral text with a muted coral seam, physically separated from routine actions; disabled controls fall to 45% opacity.

### Chips

- **Style:** Runner status is a plain inline label paired with a 9px circular lamp, not a filled pill.
- **State:** Mint means online, amber means connecting, coral means revoked, and faint neutral means offline.

### Cards / Containers

- **Corner Style:** Square for structural regions and runner channels.
- **Background:** The workplane remains on console ground; the topbar, inputs, and command output shift only one tonal step when needed.
- **Shadow Strategy:** Flat by default; only the status lamp has a shadow.
- **Border:** Hairline seams divide the overview, fleet, enrollment, list, and runner channels.
- **Internal Padding:** Runner channels use a 20px leading inset with 22px top and 24px bottom breathing room.

### Inputs / Fields

- **Style:** Control-surface fill, strong seam, 8px radius, and 10px by 12px inset.
- **Focus:** A 2px focus-ice outline with a 3px offset; the text caret uses relay cyan.
- **Error / Disabled:** Error copy becomes revocation coral; disabled buttons reduce opacity and remove the active cursor.

### Navigation

The 72px topbar is a persistent session boundary with the wordmark at left and utility actions at right. At phone width it compacts to 64px, drops the version line and manual refresh action, and keeps **Lock console** visible.

### Runner Channel

Each channel connects visually to the signal rail, then presents identity, plain-language status, a four-column measurement grid on wide screens, gateway truth, and an isolated destructive control. Status changes update in place. A successful refresh runs one 650ms cyan pulse down the rail using `cubic-bezier(.16, 1, .3, 1)`; reduced-motion preference removes it entirely.

### Enrollment Command Well

The one-time command is an inset mono-data well with a strong seam, a 230px maximum height, selectable wrapped content, and an adjacent neutral copy action. It appears only after successful token creation.

### Named Rules

**The In-Place State Rule.** Status lamps and plain-language availability copy change where they are; runner channels do not animate in from nowhere.

**The Primary Path Rule.** Keep machine enrollment visible as the main mint action, including the first mobile viewport, without giving routine or destructive actions equal emphasis.

## Do's and Don'ts

### Do:

- **Do** treat the runner fleet as one connected backplane with a signal rail and full-width channels.
- **Do** reserve routable mint, attention amber, revocation coral, and relay cyan for their operational meanings.
- **Do** use the locally bundled Space Grotesk for interface copy and the mono stack for machine-reported data.
- **Do** preserve explicit `unknown`, `not reported`, and last-seen states.
- **Do** keep **Connect machine** reachable in the first mobile viewport and keep measurement pairs free of horizontal scrolling.
- **Do** honor reduced-motion preference by removing the refresh pulse.

### Don't:

- **Don't** turn fleet channels, metrics, or enrollment into a grid of floating cards.
- **Don't** add gradients, glass, backdrop blur, decorative imagery, or ambient color.
- **Don't** use shadows to separate ordinary regions; use graphite plane changes and 1px seams.
- **Don't** use operational colors as decorative accents or interchange their meanings.
- **Don't** hide uncertain telemetry, invent capability, or style missing data as though it were unimportant.
- **Don't** let destructive actions share the filled mint silhouette of the primary path.
