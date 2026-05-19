# GRaDOS Mechanics And Elastic Metamaterials Domain Profile

Load this profile with a writing profile when the topic concerns mechanics,
elastic metamaterials, acoustic metamaterials, mechanical metamaterials,
phononic crystals, wave propagation, vibration control, band gaps, architected
materials, lattice materials, or related experimental/simulation systems.

This profile adds domain guardrails. It does not replace canonical paper reads.

## Domain Vocabulary

Useful search and claim terms include:

- elastic metamaterial, acoustic metamaterial, mechanical metamaterial,
  phononic crystal, architected material, lattice material;
- unit cell, periodic structure, finite array, dispersion relation,
  Bloch-Floquet boundary condition;
- Bragg scattering, local resonance, band gap, attenuation, transmission loss,
  mode shape, eigenfrequency, group velocity;
- effective mass, effective modulus, dynamic stiffness, damping, loss factor,
  prestress, tunable metamaterial, nonlinear response;
- finite element model, mesh convergence, boundary condition, excitation,
  measurement, laser vibrometry, shaker test, impact test, 3D printing.

## Search Seeds

Combine mechanism, method, and validation terms. Examples:

- `elastic metamaterial local resonance band gap finite element experiment`;
- `phononic crystal Bloch-Floquet dispersion transmission measurement`;
- `mechanical metamaterial vibration attenuation finite structure validation`;
- `tunable elastic metamaterial prestress bandgap experimental validation`;
- `lattice metamaterial unit cell mode shape band gap mechanism`.

## Evidence Roles To Track

For claim matrix entries, tag support with the most specific role available:

- geometry or unit-cell definition;
- material properties and fabrication method;
- boundary condition and loading/excitation;
- numerical method or solver setup;
- mesh convergence or model validation;
- dispersion or band structure calculation;
- transmission, attenuation, or frequency-response measurement;
- mode-shape interpretation;
- finite-size effect;
- damping or loss treatment;
- limitation, scale constraint, or manufacturing tolerance.

## Domain Guardrails

Hard fail:

- treating acoustic-fluid results as elastic-solid evidence without saying so;
- mixing infinite periodic band structure with finite-sample transmission
  without explaining the relation;
- claiming a complete band gap when the source shows only directional,
  partial, or mode-specific attenuation;
- presenting a simulated band gap as experimental validation;
- copying a frequency range to a new geometry/material without scaling or
  evidence;
- ignoring damping, boundary conditions, or finite-size effects when they are
  central to the cited source.

Warning:

- paper evidence differs in dimensionality, material class, unit-cell topology,
  or fabrication scale;
- result depends on an idealized periodic assumption but the user's system is
  finite or disordered;
- claim compares band gaps reported with different metrics, e.g. dispersion
  diagrams, transmission spectra, attenuation constants, or mode shapes.

## Writing Guidance

For experimental protocol tasks:

- make specimen geometry, fabrication tolerance, excitation type, sensor layout,
  and boundary condition explicit;
- separate simulation calibration, experimental measurement, and model
  validation;
- require mesh and convergence evidence for numerical workflows when possible.

For literature reviews:

- organize by mechanism and validation level, not only by chronology;
- distinguish theory/simulation-only claims from experimentally validated
  claims;
- keep Bragg and local-resonance explanations separate unless a paper explicitly
  couples them.

For manuscripts and reports:

- keep figure/table claims tied to the user's actual data or canonical paper
  assets;
- mark proposed plots as placeholders until generated from real data or
  verified simulations;
- state frequency, scale, and boundary-condition limitations near the claims
  they qualify.
