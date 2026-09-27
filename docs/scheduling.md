# Scheduling

The server's scheduler matches queued jobs with free node slots. It runs whenever something changes (a job is queued,
a node connects, a job finishes) and otherwise every few seconds. Nodes never pick up work themselves.

Every job that's waiting shows **why** in the queue, e.g. *Background work runs 23:00–07:00*,
*node-2: GPU busy (93% > 70%)*, or *No working AV1 encoder on this node*.

## Priorities

| Priority | Typical use |
|---|---|
| Critical | Something you need right now |
| High | Default for jobs you queue by hand |
| Normal | Default for rules |
| Low | Default for aging-policy stages |
| Background | Bulk archive work that should only use spare capacity |

Priorities are strict tiers: a Normal job never waits behind a Low one. Within a tier, jobs run in the order they
were queued. You can change a queued job's priority from the queue.

## Choosing a node

For each job, the scheduler considers every online node that is enabled, not paused, and has a free slot. It then drops the nodes that:
- are outside their working hours or over their utilization limits (below)
- earlier declined this job (for example, because they couldn't see the file)
- have no **verified** encoder that fits the profile

Among the rest, it prefers:
1. a node that can use a **GPU** for this profile over a CPU-only one,
2. then the node with the most free slots,
3. then the node with the lowest CPU load.

Jobs that are **replacing a file** when their node disappears stay pinned to that node until it comes back
([nodes.md](nodes.md#when-a-node-goes-away)).

## Time windows

Windows use `HH:MM` in the container's time zone (`TZ`). They may wrap past midnight (`23:00–07:00`). An optional
day list refers to the day the window **starts**: *Fri 23:00–07:00* includes Saturday 03:00. Equal start and
end times mean "all day".

Windows only control when a job may **start**. A job that is already running when its window closes finishes normally.

### Quiet hours (Settings)

Quiet hours are a global window for low-priority work. Pick what they apply to:

- **Background only:** Background jobs only start inside the window.
- **Low and below:** Low and Background jobs only start inside the window.

Normal and higher priorities, and every job you queue by hand, ignore quiet hours.

### Rule windows

A rule (or aging policy) can carry its own window. Jobs created by that rule only start inside it, whatever their priority.

### Node time windows

On a node's page, **Working hours** limit when that node starts jobs. For example, a gaming PC could work only
from 01:00 to 08:00.

## Utilization limits

A node can be told not to start work while its machine is busy with *something else*:

- **Max GPU utilization:** don't start a job while the GPU is above this percentage.
- **Max CPU utilization:** likewise for the CPU.

These limits are only checked while the node is running **no FrameForge jobs**. Otherwise FrameForge's own load would
block its second slot. If a GPU's utilization can't be read (Intel), the GPU limit has no effect.

## Concurrency

Each node runs up to *max concurrent jobs* at once. With **Reserve last slot for Normal+**, Background and Low jobs can
use every slot but the last, so something urgent can always start right away.

## Retries

Some failures are usually temporary. They are retried automatically, up to **Settings → Max attempts** (default 2):

| Failure | Retried on |
|---|---|
| NVIDIA encoder busy (session limit), out of memory, hardware decode pipeline failed | another node (the failing node is excluded for this job) |
| Interrupted replacement that was rolled back cleanly | another node (as above) |
| Node went offline mid-job | any node, including the same one once it's back |

Hardware decoding problems are also handled *inside* a job: when a file won't decode on the GPU, the node restarts the
encode with software decoding before it reports a failure.

All other failures (damaged source, permissions, disk full, output conflict) stop, with a diagnosis
([troubleshooting.md](troubleshooting.md)). Fix the cause, then press **Retry** on the job.

If a node accepts a job but never starts it within 90 seconds, the job is requeued without counting an attempt.
