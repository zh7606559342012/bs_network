#!/bin/bash

AGENT_SERVICES=(monitor_agent)

stop_all() {
   systemctl stop monitor_agentwatching.timer

   max=$(( ${#AGENT_SERVICES[@]} -1 ))
   while [[ max -ge 0 ]]
   do
       nf="${AGENT_SERVICES[$max]}"
       systemctl stop "${nf}"
       (( max-- ))
   done
}

stop_all