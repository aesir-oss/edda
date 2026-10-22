---
type: Concept
title: Attention mechanism
description: How transformers weight parts of their input when computing each output.
tags: [ml, transformers]
sources:
  - id: vaswani2017-s3-2
    resource: https://arxiv.org/abs/1706.03762
    title: Attention Is All You Need
  - id: notes-attention
    resource: raw:887e1bdc
    title: Reading notes on attention
verified:
  - {by: owner, at: 2026-10-21T14:10:00Z}
status: stable
edda_rev: 1
---
# Attention mechanism

Attention computes a weighted average of values, with weights from how well a query matches each key.[^notes-attention]
Scores are dot products divided by the square root of the key size, which keeps the softmax useful for long
vectors.[^notes-attention]

Several attention heads run in parallel and their outputs are concatenated; the
[Transformer](/transformer.md) uses this throughout.[^vaswani2017-s3-2]

When the queries, keys and values all come from the same sequence it is called self-attention.[^notes-attention]

[^vaswani2017-s3-2]: Section 3.2.
[^notes-attention]: Lines 3 to 6.
