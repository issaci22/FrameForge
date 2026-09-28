# Nodes

A **node** is a process that transcodes. Every installation has at least one:

- **Built-in node:** runs inside the server container when `FF_ROLE=all`. It needs no setup.
- **Remote nodes:** extra containers (`FF_ROLE=node`) on other machines, or on the same machine with a different GPU.

The server decides what runs where ([scheduling.md](scheduling.md)). Nodes do the work and report back.

## How a node connects

The node opens an **outbound** WebSocket to `FF_SERVER_URL` and authenticates with its node token. The server
pushes jobs over that connection. The node streams progress, logs, metrics and results back. You never need to
open a port on a node machine.

Node tokens (`ffn_…`) only allow a node connection. They can't be used to log in or call the API. The server
stores only a hash of each token. The plaintext is shown once, when you create the node or regenerate its token.

## Add a remote node

1. **Settings → Server URL for nodes:** set the address other machines use to reach the server, e.g.
   `http://192.168.1.20:8686`. Without it, the generated snippets use whatever address your browser used, often
   `localhost`, which a remote machine can't reach. The wizard shows a warning when the snippet's address is
   `localhost`, `127.0.0.1`, `::1` or `0.0.0.0`. Fix the setting and regenerate the token on the node's page, or
   edit `FF_SERVER_URL` in the snippet.
2. **Nodes → Add node:** choose a name and hardware type. Copy the token and the generated compose or `docker run` snippet.
3. On the node machine, save the snippet as `docker-compose.yml`. It already uses the server's exact version
   (for example `ghcr.io/issaci22/frameforge:0.1.0`). Nothing needs to be built. To start from
   [`docker/compose/node.yml`](../docker/compose/node.yml) instead, set its image to the tag the server runs
   (**Settings → About this server** shows the version).
4. Mount the media (next section), check the token, and start it: `docker compose up -d`
   (or `docker compose -f docker/compose/node.yml up -d`).

The node appears as **online** within seconds, with its verified encoders listed. If it doesn't, check
`docker logs frameforge-node`. An invalid token is rejected with *Invalid node token*. A version mismatch is
rejected with *Protocol mismatch … Update the node image*.

## Shared storage and path mappings

Nodes don't receive files over the network. **Every node must mount the same media the server sees**, usually from
the same NAS share. FrameForge has no file-transfer mode yet.

If a node mounts the media at a different path, add a **path mapping** on the node's page:

| Server path | Node path |
|---|---|
| `/media/vods` | `/mnt/vods` |

Before a job is sent, its paths are translated with the longest matching mapping. Job results, events and warnings
use server paths (e.g. *Moving the original to /media/vods/.frameforge-originals/…*), so they match your library.
Raw FFmpeg and OS errors under *Technical details* show the node's own paths. Mappings match whole folder names: `/media/vods` doesn't match
`/media/vods-old`.

The node writes outputs and moves originals, so its mount must be **read-write** and its `PUID`/`PGID` must
be allowed to write there.

## Node settings

| Setting | Effect |
|---|---|
| Max concurrent jobs | How many jobs this node runs at once. The node page shows a suggested value from detection: 2 for a GPU, and CPU threads ÷ 12 (1 to 3) for CPU-only nodes. |
| Paused | Finishes running jobs, starts no new ones |
| Enabled | Disabled nodes are refused at connect time |
| Reserve last slot for Normal+ | With 2+ slots, Background/Low jobs can't take the last free slot |
| Working hours | The node only starts jobs inside this window ([scheduling.md](scheduling.md#node-time-windows)) |
| Max GPU / CPU utilization | Don't start work while the machine is busy with something else ([scheduling.md](scheduling.md#utilization-limits)) |
| Re-detect | Re-runs hardware detection, e.g. after a driver update |

## When a node goes away

| What happened | What FrameForge does |
|---|---|
| Node restarts during a transcode | When it reconnects, the job is requeued (no attempt counted) |
| Node stays offline for over 2 minutes | Running jobs are requeued, counting an attempt, and can run on another node |
| Node dies while replacing a file | The job stays **pinned to that node**. When the node reconnects, the server sends it a recovery request, and the node completes or rolls back the replacement from its on-disk journal. Such a job never moves to another node. |
| Node finished a job while the server was down | The node holds the result in memory and reports it after reconnecting, so the file isn't encoded twice. If the node restarted too and the job is sent again, the node refuses it because the source no longer matches what was scanned. |

A failed or interrupted transcode never touches the source file. Leftover partial output in `.frameforge-tmp`
is overwritten when the job runs again.

Recovery needs nothing but the media itself: the finalize journal is written next to the output
(`.frameforge-tmp/job-N.mkv.journal`), and the server re-sends the plan. A node's `/config` only caches
hardware-detection samples.

## Using two GPUs in one machine

Run one node container per GPU and pass each one only its own render node. See [gpu.md](gpu.md#several-gpus-in-one-machine).
