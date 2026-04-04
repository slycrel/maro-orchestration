---
name: data_analysis
description: "Analyze a dataset or structured output: clean, explore, extract patterns, and produce conclusions"
roles_allowed: [worker, short]
triggers: [analyze, analyse, data analysis, statistics, patterns, trends, metrics, evaluate results, measure, quantify]
---

## Overview

Use this skill when a goal requires turning raw data (files, API responses, logs) into insight. Emphasis on honest uncertainty — don't over-claim.

## Steps

1. **Understand the schema** — list the columns/fields, their types, and expected value ranges before computing anything.
2. **Check for data quality issues** — nulls, outliers, duplicates, and encoding problems. Note but don't necessarily fix them unless they affect the analysis goal.
3. **Describe the distribution** — count, min, max, mean/median for numeric fields. Top-N for categorical fields.
4. **State the question explicitly** — "I am trying to determine whether X correlates with Y" before computing.
5. **Compute the relevant statistics** — use the simplest method that answers the question. Avoid complexity theater.
6. **Interpret the numbers** — what does the output mean in plain language? What is the effect size?
7. **State limitations** — sample size, data recency, confounders, and what the data cannot tell you.
8. **Produce a conclusion** — one paragraph answering the original goal question, with confidence level.

## Output format

- Lead with the conclusion, not the methodology.
- Include a "Limitations" section.
- If a chart would help, describe it in text (no rendering in headless mode).
