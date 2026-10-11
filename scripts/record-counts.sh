#!/bin/sh
# Needs the stock fp worker (iii trigger compose::add worker=fp@0.2.6) and a running engine.
iii trigger fp::pipe --json '{
  "through": [
    {"function": "audit::read"},
    {"function": "fp::get", "payload": {"path": "/entries"}},
    {"function": "fp::countBy", "payload": {"path": "/kind"}}
  ],
  "preview_chars": 400
}'
