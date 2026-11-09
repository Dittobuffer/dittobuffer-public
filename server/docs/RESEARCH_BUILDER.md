# Research Builder

Use **Seasons & Events > Research Builder** in the existing admin console. [KNOWN-ISSUES.md](KNOWN-ISSUES.md) distinguishes actual Android evidence from automated tests.

## Build a quest

Choose Field, Special, Timed, Collection, branching, timed branching or Breakthrough Research; set its title, dates and eligibility, add steps, then select registered objectives and rewards. Ordinary story steps support one to three tasks; verified level gates retain their four immutable source objectives. Steps can be duplicated, dragged or moved with arrows. Field Research has exactly one step and one task; its task and step rewards are combined into the single native Field claim. Some advanced eligibility/filter fields currently require declarative import; they are not all present as form controls.

Each objective needs a positive bounded target, original description and supported reward. Catch/throw conditions support species, types, shiny state, weather boost, ball, curveball and minimum Nice/Great/Excellent quality. Gameplay supplies authoritative spawn metadata; nonfinite throw quality fails the filter. A missed throw does not advance landed-throw tasks. A Research description never executes code.

Available successful-action counters: catch, landed throw, Pok?Stop spin, raid victory, Route completion, Rocket Grunt/Leader/Giovanni victories, Shadow catch, purification, Radar assembly, Max collection/visit/victory/move unlock/upgrade, hatching, evolution, power-up, transfer, Buddy candy, sending/opening Gifts and pinning Postcards. Existing modules must be enabled. The shared registry also exposes verified whole kilometres, Gym/Trainer wins, trades, Buddy hearts, Mega Evolution, Zygarde Cells and super-effective Trainer Charged Attacks. Generic unique-stop, area exploration, Buddy feeding and Party counters remain unavailable. Physical walking is not newly Pixel-verified. An available task is not evidence of new native acceptance for every action.

## Rewards

Supported native grants: XP, Stardust, the explicitly listed Ball/medicine/berry/incubator/Rocket items, family Candy, XL Candy, supported Mega Energy and Pokemon encounters. The menu uses existing item IDs; Master Ball is excluded. Rare Candy, Raid Passes, Max Particles, event medals and cosmetic entitlements are not yet supported research reward grants and must not be added as arbitrary IDs.

Encounter rewards support species, restored form, fixed level and shiny probability. Existing verified M23 cinematics may be selected when their resource closure is complete. Cinematics require Special Research and an exact registered species/selector; an arbitrary Victini reward does not imply a working cinematic. IV policy remains the existing Research 10-15 range; arbitrary IV/catch-policy editing is not implemented.

## Ownership and revision safety

Studio owns content definitions only. Runtime enrollment uses the existing `research_stories`, `research_tasks`, `research_visits`, `research_stage_claims`, `research_encounters`, stamps and canonical inventory tables. No competing player progress database was introduced. Each enrolled story and earned Field task freezes its definition. Editing a live definition affects future enrollment rather than silently resetting existing progress.

Field tasks are earned from native Pok?Stop spins with normal daily/slot limits. Story objectives progress through successful authoritative gameplay events. Retry guards precede new Gift/transfer/etc. counters. Rewards use existing RPC transactions and ownership/capacity checks. Claims persist before acknowledgment; fresh request IDs cannot repeat a claimed reward. Encounter ownership remains account-specific.

A Special storyline remains available to enrolled players after its event ends; this is permanent progress, not a substitute for expiring Timed Research. Field tasks already earned also retain their frozen definition. Parent/event eligibility gates future enrollment.

## M25 native layouts and limits

M25 adds verified native Events adapters for Timed quests (section 6), Collection Challenges (section 9), and backend support for timed branching (section 18). Field, seven-stamp Breakthrough, multistage Special, Timed, Collection, permanent branching and a Shaymin cinematic reward were exercised on the Pixel 5. Timed branching remains backend-verified only. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md) for remaining gaps.

Timed/Collection/timed-branching windows are start-inclusive and end-exclusive. Completed but unclaimed rewards expire; already granted encounter entitlements survive removal. Special stories remain permanent after enrollment. A seventh stamp freezes its Breakthrough pool and selected encounter before claim. Calendar days use configured IANA time zones, including DST.

Collection menus select species, methods and form. Advanced JSON supports gender, alignment, costume and Max filters. Existing Storage never counts as a new acquisition. Branch selection freezes one exclusive path; its selection and claims share the existing transaction/receipt system. Branch chooser styling and completed archives have remaining native limitations recorded in the acceptance report.

**Preview research** is read-only: tasks, stages, paths, rewards, prerequisites, expiration and resource/feasibility warnings. It never grants rewards. **Level gates 71-80** is a read-only source reference: administrators cannot replace verified gate requirements in the content editor. Six original import examples are under `config/liveops-examples/research/`, also available in Templates. They are drafts until separately published.

The M33 Pixel acceptance earned a Season task from a real spin, completed it through three catches, then claimed its native 300 XP award and stamp. This task was enrolled before the Field stage-reward bundling correction and correctly retained its earlier frozen reward. Later tasks use the corrected bundled configuration.


## M25.1 native presentation follow-up

Branch cards now use original locally served card art to avoid the native white-mask/white-text combination. Completed stories retain distinct archive titles. Claimed Collection Challenges project durable Elite Collector count/history; incomplete challenges do not count. Daily/streak gates refresh on local date changes without discarding completed unclaimed criteria. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md). Native Collection `Ended` wording, stable-ID history labels and confirmation illustration polish remain open.
