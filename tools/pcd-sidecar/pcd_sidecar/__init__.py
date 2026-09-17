"""PCD sidecar: a local drop-in alternative to TypeSafe's Jev /v1/systemone API.

Scores constrained decisions (noul/choice/score questions) against a small
open model by prefilling the prompt once and scoring each candidate answer's
complete token sequence from the logits -- no free-text generation, no
first-token shortcuts. See README.md for the full design.
"""

__version__ = "0.1.0"
