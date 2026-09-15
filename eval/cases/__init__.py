"""Registry of live eval cases.

This package is where the actual eval-case definitions live. ``CASES`` is the
single registry ``eval.runner`` iterates. Each case-definition module under
this package appends its ``EvalCase`` objects to ``CASES`` at import time;
``eval.cases.__init__`` imports those submodules *after* ``CASES`` is created
(Python executes the ``__init__`` body top to bottom, so ``CASES`` exists
before any submodule import starts, and each submodule's
``from eval.cases import CASES`` therefore receives the already-initialized
list). Adding a new case means adding a new module to this package and
importing it at the bottom of this file.

``EvalCase`` / ``CheckResult`` are re-exported here so case-definition modules
can import everything they need from one place
(``from eval.cases import CASES, EvalCase, CheckResult``).
"""

from __future__ import annotations

from eval.case import CheckResult, EvalCase

# Every registered eval case, in registration order. ``eval.runner`` iterates
# this list; case modules append to it (see the module docstring).
CASES: list[EvalCase] = []

# Case-definition modules register themselves by appending to CASES at import
# time. They are imported after the CASES list above is created.
from eval.cases import (  # noqa: E402,F401  (import for registration side effect)
    avg_price_per_customer_uk_2011_case,
    avg_quantity_2011_case,
    avg_unit_price_case,
    customer_grouped_uk_2011_case,
    grouped_country_2011_case,
    net_scalar_uk_2011_case,
)

