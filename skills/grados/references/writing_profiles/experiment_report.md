# GRaDOS Experiment Report Profile

Use this profile when the user has experiment, simulation, or analysis results
and wants a report, result section, discussion section, or paper-style writeup.

GRaDOS can ground background, method comparison, interpretation context, and
limitations in papers. It cannot invent the user's data.

## Deliverable

Default output:

- research question and evidence-backed context;
- user-data provenance summary;
- method or setup description;
- results written only from supplied data;
- comparison with paper evidence;
- limitations and alternative explanations;
- figure/table manifest;
- evidence status and unresolved claims.

## Workflow

1. Separate sources of truth:
   - user data: measurements, simulation outputs, plots, logs, notebooks, and
     user-supplied observations;
   - paper evidence: background, comparable methods, expected mechanisms,
     validation practices, and limitation context;
   - model prose: drafting only, never evidence.
2. Ask for or inspect the user's data files when the report needs numerical
   values, plots, or tables. If data is unavailable, write placeholders.
3. Search and extract papers for method context and interpretation, not for
   fabricated confirmation of the user's results.
4. Build claim matrix entries for:
   `background`, `method_context`, `comparison`, `interpretation`, and
   `limitation`.
5. Draft results from user data first, then discussion from the intersection of
   user data and canonical paper evidence.
6. Audit each comparison or interpretation claim against the claim matrix.

## Report Gates

Hard fail:

- invented numerical results, uncertainty bars, fitted parameters, sample sizes,
  mesh convergence values, or statistical tests;
- generated figures or tables presented as measured or simulated data;
- paper evidence used to imply that the user's unobserved result occurred;
- no provenance for a figure, table, or dataset-dependent claim.

Warning:

- user data lacks metadata needed for reproducibility;
- literature comparison uses a different geometry, material, scale, boundary
  condition, or measurement metric;
- result interpretation has multiple plausible mechanisms in the literature.

## Figure And Table Manifest

For every figure/table, record:

- id and caption;
- data source or placeholder status;
- generation script/notebook path if available;
- literature anchors used for comparison or expected trend;
- claims that rely on the figure/table.
