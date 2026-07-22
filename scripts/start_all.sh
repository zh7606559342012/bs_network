#!/bin/bash

AGENT_SERVICES=(monitor_agent)

start_all() {
   systemctl daemon-reload
   for nf in "${AGENT_SERVICES[@]}"; do
       systemctl start "${nf}"
   done
   systemctl start monitor_agentwatching.timer
}

start_all
