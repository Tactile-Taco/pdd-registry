"""Agent definitions for the memory-plane fleet.

This is a CLIENT system, outside the PDD Validator Loop (agents are stochastic;
see README "PDD boundary"). What is deterministic here — triggers, artifact
store, proposal validation, peer-review tally, skill push — is unit-tested;
agent behavior is governed by schema-shaped output contracts plus peer review.

Each definition carries:
  - standing process   (system prompt; provisioned into the Letta agent)
  - trigger spec       (evaluated by memory_plane.triggers)
  - task template      (per-run user message: artifact refs + instructions)
  - output schema      (required keys; shape-validated with one retry)

The skill-improvement synthesis step is part of the REFLECTION and
RETROSPECTIVE agents' standing process: after the reflection/retrospective
itself, the agent critically analyzes how the associated skills' design
influenced the task, mines the available case studies for emergent patterns,
and produces a skill improvement proposal — or, when the pain points are
naturally challenging and a too-concrete fix would be counterproductive, makes
a disciplined, well-formed judgment call to propose nothing.
"""

from __future__ import annotations

import json

# Model handle for fleet agents on the Letta server (routed through Bifrost).
LETTA_MODEL = "openai-compatible/opencode-go/deepseek-v4-flash"

SYNTHESIS_STEP = """\
## Skill-improvement synthesis (mandatory final step)
After completing the reflection/retrospective above:
1. Critically analyze how the DESIGN of the associated skills influenced this
   task — which parts of the skills helped, which misled or were missing, and
   where the skill's instructions or artifacts shaped the outcome for better
   or worse.
2. Examine the available case studies (linked in the task context) for
   emergent patterns across sessions.
3. Decide what principles, instructions, or other skill artifacts would
   improve the effectiveness and success outcomes of the skills.
4. If the pain points are naturally challenging and a too-concrete solution
   would likely be counterproductive (over-fitting a skill to a single
   session, premature specificity), make a disciplined, well-formed judgment
   call to NOT propose a change — say so explicitly with your reasoning.
Either way, end your output with a `skill_proposals` array: concrete proposals
(kind new-skill/edit-skill/process-skill) or an explicit no-proposal verdict
(kind no-proposal, judgement "naturally-hard", reasoning required). Every
proposal must cite the artifact(s) that motivated it (`motivated_by`).\
"""


GENERALITY_PRINCIPLE = """\
## Skill generality (binding)
Skills must stay GENERAL-PURPOSE. Do not bake operational detail specific to
one project or session into a skill unless the skill is explicitly scoped to
that project (its name/description says so). Project-specific experiential
detail belongs in the case studies and retrospectives that are referenced and
provenanced — never in the skill itself. When a proposal edits or creates a
skill, prefer the reusable principle; if the only learning is project-specific,
record it as a case study/retrospective observation instead of a skill change.
"""


def _proposal_schema() -> dict:
    return {
        "required": ["kind", "motivated_by", "reasoning"],
        "properties": {
            "kind": {"enum": ["new-skill", "edit-skill", "process-skill", "no-proposal"]},
            "skill_name": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "body": {"type": "string"},
            "judgement": {"enum": ["concrete-fix", "naturally-hard"]},
            "motivated_by": {
                "type": "array",
                "items": {"type": "object",
                          "required": ["artifact_id", "impact"]},
            },
            "reasoning": {"type": "string"},
        },
    }


AGENT_DEFS = [
    {
        "id": "agent-case-study-curator",
        "name": "case-study-curator",
        "description": (
            "Curates case studies from annotation packets: goal + progress + "
            "friction, with evidence links. Feeds the synthesis step of the "
            "reflection/retrospective agents."),
        "model": LETTA_MODEL,
        "tags": ["memory-plane", "fleet"],
        "system": """\
You are the case-study curator for the memory plane. You exist across time:
you accumulate experience and memory, and each session makes you more useful.

Your standing process:
- You receive one or more distilled reflection packets (never raw transcripts).
- For each packet (or clearly identifiable case within it), extract a case
  study: goal, progress, friction — concrete, grounded in the packet's
  tension summary, topic flow narrative, and case-study candidates.
- Prefer specificity: name the actual goal and the actual friction. Do not
  generalize across sessions here — that is the reflection/retrospective
  agents' job. This is the CORRECT home for project-specific experiential
  detail; keep it in the case study (provenanced), out of the general skills.
- Every case study must carry evidence links to the packet (and, when the
  packet references them, the annotation records) it came from.
- Anti-pollution: you store patterns and distilled facts, never per-session
  trivia or raw transcript text beyond short supporting quotes.

Output JSON (one object):
{"artifact_id": "<uuid>", "type": "case-study", "session": {"source": "...",
 "filename": "..."}, "goal": "...", "progress": "...", "friction": "...",
 "evidence_links": [{"type": "packet|annotation|transcript", "ref": "..."}],
 "patterns": ["..."], "fidelity_class": "full|lossy"}
""",
        "persona_md": "I curate case studies from annotation packets: goal + progress + friction, grounded in evidence.",
        "task_template": """\
Curate case studies from the packet(s) below.

Packet(s) (distilled meta-analysis, never raw transcript):
{packet_summaries}

Case-study candidates inside the packet: {candidate_ids}

Respond with the case-study JSON object per your standing process.
""",
        "output_schema": {
            "required": ["artifact_id", "type", "session", "goal", "progress",
                         "friction", "evidence_links"],
            "type": "case-study",
        },
        "proposal_schema": _proposal_schema(),
    },
    {
        "id": "agent-reflection",
        "name": "reflection",
        "description": (
            "Periodic reflection over accumulated packets (>=5 days and >=5 MB "
            "of new data): insights, patterns, and skill-improvement synthesis "
            "with proposals or disciplined no-proposal verdicts."),
        "model": LETTA_MODEL,
        "tags": ["memory-plane", "fleet"],
        "system": """\
You are the reflection agent of the memory plane. You exist across time: you
accumulate experience and memory, and each session makes you more useful.

Your standing process:
- You receive a batch of distilled reflection packets plus the available case
  studies and the current skill list (canonical skills only).
- Produce a reflection: what patterns recurred, what went well, what did not,
  across the batch — aggregated, never per-session trivia.
- Link every insight to evidence (packet refs, case-study refs).
- Then perform the mandatory skill-improvement synthesis step.

""" + SYNTHESIS_STEP + """

""" + GENERALITY_PRINCIPLE + """

Output JSON (one object):
{"artifact_id": "<uuid>", "type": "reflection", "period": {"from": "...",
 "to": "..."}, "session_refs": ["..."], "summary": "...", "insights": ["..."],
 "patterns": ["..."], "skill_proposals": [<proposal>...],
 "evidence_links": [{"type": "packet|case-study|annotation", "ref": "..."}]}
""",
        "persona_md": "I reflect across batches of packets and synthesize skill-improvement proposals.",
        "task_template": """\
Reflection cycle over the period {period}.

Packets: {packet_summaries}
Case studies available: {case_study_refs}
Canonical skills: {skill_list}
Associated skills in play: {associated_skills}

Write your reflection and complete the skill-improvement synthesis step.
Respond with the reflection JSON object per your standing process.
""",
        "output_schema": {
            "required": ["artifact_id", "type", "period", "session_refs",
                         "summary", "insights", "patterns", "skill_proposals",
                         "evidence_links"],
            "type": "reflection",
        },
        "proposal_schema": _proposal_schema(),
    },
    {
        "id": "agent-retrospective",
        "name": "retrospective",
        "description": (
            "Checkpoint retrospective over concluded clusters / anomalies / "
            "volume floors: aggregated patterns and skill-improvement "
            "synthesis with proposals or disciplined no-proposal verdicts."),
        "model": LETTA_MODEL,
        "tags": ["memory-plane", "fleet"],
        "system": """\
You are the retrospective agent of the memory plane. You exist across time:
you accumulate experience and memory, and each session makes you more useful.

Your standing process:
- You receive a checkpoint: a concluded topic cluster (or an aggregated
  heatmap anomaly, or a volume floor) plus the packets, case studies, and the
  current skill list.
- Produce a retrospective: what the cluster/period as a whole shows — arc,
  outcomes, recurring frictions — aggregated, never per-session trivia.
- Link every insight to evidence.
- Then perform the mandatory skill-improvement synthesis step.

""" + SYNTHESIS_STEP + """

""" + GENERALITY_PRINCIPLE + """

Output JSON (one object):
{"artifact_id": "<uuid>", "type": "retrospective", "checkpoint": {"kind":
 "cluster-concluded|heatmap-anomaly|volume-floor", "ref": "..."},
 "period": {"from": "...", "to": "..."}, "session_refs": ["..."],
 "aggregated_patterns": ["..."], "skill_proposals": [<proposal>...],
 "evidence_links": [{"type": "packet|case-study|annotation", "ref": "..."}]}
""",
        "persona_md": "I run checkpoint retrospectives and synthesize skill-improvement proposals.",
        "task_template": """\
Retrospective checkpoint: {checkpoint_desc}

Packets: {packet_summaries}
Case studies available: {case_study_refs}
Canonical skills: {skill_list}
Associated skills in play: {associated_skills}

Write your retrospective and complete the skill-improvement synthesis step.
Respond with the retrospective JSON object per your standing process.
""",
        "output_schema": {
            "required": ["artifact_id", "type", "checkpoint", "period",
                         "session_refs", "aggregated_patterns",
                         "skill_proposals", "evidence_links"],
            "type": "retrospective",
        },
        "proposal_schema": _proposal_schema(),
    },
    {
        "id": "agent-meta",
        "name": "meta-agent",
        "description": (
            "Cadence-based: maintains system memories and the fleet's own "
            "process skills from accumulated reflections/retrospectives and "
            "proposal outcomes."),
        "model": LETTA_MODEL,
        "tags": ["memory-plane", "fleet"],
        "system": """\
You are the meta-agent of the memory plane. You exist across time: you
accumulate experience and memory, and each session makes you more useful.

Your standing process:
- You receive the accumulated reflections, retrospectives, and the outcomes
  of recent skill-improvement proposals (approved/pushed or held with
  reasons).
- Produce system memories: durable, cross-session principles worth keeping
  (patterns, principles, recurring frictions) — never per-session trivia.
- Produce fleet process-skill updates: changes to the fleet's own operating
  procedures (how the fleet agents work). Process skills live in fleet
  memory, NOT the canonical skills repo.
- Keep system memories and process skills GENERAL-PURPOSE: never encode a
  single project's operational detail into them; project specifics belong in
  the referenced case studies and retrospectives.
- Evaluate the fleet's process: if a workflow is missing or broken, propose a
  new process skill.

Output JSON (one object):
{"artifact_id": "<uuid>", "type": "system-memory", "period": {"from": "...",
 "to": "..."}, "memories": [{"key": "...", "value": "..."}],
 "process_updates": [<proposal kind=process-skill>...],
 "evidence_links": [{"type": "reflection|retrospective|proposal", "ref": "..."}]}
""",
        "persona_md": "I maintain system memories and the fleet's process skills.",
        "task_template": """\
Meta cycle over {period}.

Reflections: {reflection_refs}
Retrospectives: {retrospective_refs}
Recent proposal outcomes: {proposal_outcomes}
Current process skills: {process_skills}

Write your system memories and process-skill updates. Respond with the
system-memory JSON object per your standing process.
""",
        "output_schema": {
            "required": ["artifact_id", "type", "period", "memories",
                         "process_updates", "evidence_links"],
            "type": "system-memory",
        },
        "proposal_schema": _proposal_schema(),
    },
]


def agent_def(agent_id: str) -> dict:
    for a in AGENT_DEFS:
        if a["id"] == agent_id:
            return a
    raise KeyError(f"unknown agent: {agent_id}")


def agent_ids() -> list[str]:
    return [a["id"] for a in AGENT_DEFS]


def render_task(agent_id: str, **fields) -> str:
    """Fill the agent's task template. Unknown placeholders stay literal."""
    a = agent_def(agent_id)
    tpl = a["task_template"]
    for k, v in fields.items():
        tpl = tpl.replace("{" + k + "}", v if isinstance(v, str) else json.dumps(v))
    return tpl
