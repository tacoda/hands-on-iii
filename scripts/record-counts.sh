#!/bin/sh
# Needs the stock fp worker (iii call compose::add worker=fp@0.3.0) and a running engine.
iii call fp::pipe --json '{
  "through": [
    {"function": "audit::read"},
    {"function": "fp::get", "payload": {"path": "/entries"}},
    {"function": "fp::countBy", "payload": {"path": "/kind"}}
  ],
  "preview_chars": 400
}'
