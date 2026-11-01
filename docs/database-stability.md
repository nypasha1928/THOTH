# SQLite connection lifetime repair

The saved Nov 1 account pause reports SQLite code 14 (CANTOPEN) while opening
the execution journal. That establishes a database-open failure, not a strategy
rejection or proof that the process restarted. The server's descriptor, memory,
disk and restart observations are still needed to confirm its immediate cause.

A reproduced application resource defect is fixed here: Python's native SQLite
connection context commits/rolls back but does not close the connection. The old
execution-journal reader opened one connection for its list, then one per order.
With garbage collection disabled to expose lifecycle dependence, one 300-order
read left 301 additional file descriptors awaiting collection. The corrected
read leaves zero. A journal read now uses one SQL snapshot query.

Long-running application database users now use a connection whose context exit
commits/rolls back with native SQLite behavior and always closes, including failed
commits. The paper-engine transaction already closed explicitly and is unchanged.
Opening failures retain curated file-descriptor/limit and disk-free counts where
available; database paths, SQL and exception text are not included in reports.

Tests cover commit, rollback, failed commit, privacy of resource diagnostics, and
20 reads of a 3,000-order journal under a 128-file limit with GC disabled. File
handles remain flat. No order retries, risk limits, account safety locks, loss
cooldowns or restart-entry activation policies are changed.

Before deployment, capture current server observations (no account data):

```bash
docker exec thoth-app-1 sh -c 'echo "Open file limit:"; ulimit -n; echo "Server open files:"; ls /proc/1/fd | wc -l; echo "Disk space:"; df -h /app/data'
docker inspect -f 'RestartCount={{.RestartCount}} OOMKilled={{.State.OOMKilled}} StartedAt={{.State.StartedAt}}' thoth-app-1
```

Repeat the descriptor check after deployment and after sustained operation.
The deployment itself restarts the container; check readiness and manually enable
new entries afterward. Container recreation resets Docker's restart count, so a
fresh count of zero alone does not establish the cause of earlier interruptions.
