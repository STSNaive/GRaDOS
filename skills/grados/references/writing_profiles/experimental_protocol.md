# GRaDOS Experimental Protocol Profile

Use this profile when the user asks for a literature-grounded experiment,
simulation, validation, or measurement protocol. The goal is a practical
protocol that is traceable to real papers, not a finished manuscript.

## Deliverable

Default output:

- objective and scope;
- literature-backed rationale;
- system, specimen, model, or device definition;
- variables, controls, and parameter ranges;
- procedure or simulation workflow;
- measurement or output metrics;
- analysis plan;
- acceptance criteria;
- risk, limitation, and missing-evidence notes;
- evidence status and next-step checklist.

Use placeholders for user-specific geometry, material constants, equipment,
mesh settings, loading amplitudes, fabrication tolerances, and result values
unless they are supplied by the user or directly supported by canonical paper
evidence.

## Workflow

1. Clarify the physical target: phenomenon, mechanism, sample/model type,
   length scale, expected output, and whether the task is experimental,
   numerical, analytical, or mixed.
2. Search and extract papers that report comparable methods, not only similar
   conclusions. Prioritize methods sections, validation sections, figure/table
   captions, and supplementary material when available.
3. Build claim matrix entries for design choices:
   `method_context`, `parameter_range`, `boundary_condition`, `measurement`,
   `validation`, and `limitation`.
4. Draft the protocol from the claim matrix. Each factual design choice should
   point to a canonical paper window or be marked as user-supplied/open.
5. Run the section gate on the method rationale and parameter choices before
   presenting the protocol as ready to execute.

## Protocol Gates

Hard fail:

- invented parameter ranges, material constants, geometry values, or equipment
  settings;
- simulated results, experimental trends, or plots stated as if already
  observed;
- a protocol step justified only by a helper summary, abstract snippet, external
  reviewer, or model memory;
- no separation between literature evidence and user-provided assumptions.

Warning:

- evidence supports a similar but not identical material, geometry, boundary
  condition, or frequency regime;
- method is supported only by simulation papers while the user needs a physical
  experiment;
- method is supported only by experiments while the user needs a numerical
  workflow.

## Output Rule

When evidence is incomplete, keep the protocol useful by naming the missing
quantity and the paper type needed to fill it. Do not turn missing information
into confident prose.
