---
type: Concept
title: Transformer
description: A sequence model built from stacked attention layers instead of recurrence.
tags: [ml, transformers, architecture]
sources:
  - id: vaswani2017-s3
    resource: https://arxiv.org/abs/1706.03762
    title: Attention Is All You Need
status: stable
edda_rev: 1
---
# Transformer

A transformer is an encoder and a decoder, each a stack of identical layers built around
[attention](/attention.md).[^vaswani2017-s3] It has no recurrence, so positions are added to the inputs with fixed
sine and cosine encodings.[^vaswani2017-s3]

It was introduced in [Attention Is All You Need](/papers/attention-is-all-you-need.md).

[^vaswani2017-s3]: Sections 3 and 3.5.
