import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentEvent } from "./events";
import { runEndMs } from "./events";
import type { RunPhase } from "./useAgentRun";

export interface RunClock {
  t: number; // current time in ms
  end: number; // run length in ms
  playing: boolean;
  live: boolean;
  play: () => void;
  scrub: (ms: number) => void;
}

const REDUCED =
  typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The shared clock. During `live` it follows the wall clock (so the agent's
 *  thinking reads as motion even between events); on `done` it snaps to the end
 *  and becomes a scrubbable replay of the same log. Every layer renders at f(t). */
export function useRunClock(phase: RunPhase, log: AgentEvent[]): RunClock {
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const raf = useRef(0);
  const liveStart = useRef(0);
  const end = runEndMs(log);
  const endRef = useRef(end);
  endRef.current = end;

  // Live: advance t by wall-clock elapsed since the run started.
  useEffect(() => {
    if (phase !== "live") return;
    liveStart.current = performance.now();
    setT(0);
    const tick = () => {
      setT(performance.now() - liveStart.current);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [phase]);

  // Entering replay: stop, snap to the end so the whole run is visible.
  useEffect(() => {
    if (phase === "replay") {
      cancelAnimationFrame(raf.current);
      setPlaying(false);
      setT(endRef.current);
    } else if (phase === "idle") {
      setT(0);
    }
  }, [phase]);

  const play = useCallback(() => {
    if (phase !== "replay") return;
    if (playing) {
      cancelAnimationFrame(raf.current);
      setPlaying(false);
      return;
    }
    const total = endRef.current;
    if (total <= 0) return;
    if (REDUCED) {
      setT(total);
      return;
    }
    const base = t >= total ? 0 : t;
    const startWall = performance.now();
    setPlaying(true);
    const tick = () => {
      const cur = base + (performance.now() - startWall);
      if (cur >= total) {
        setT(total);
        setPlaying(false);
        return;
      }
      setT(cur);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [phase, playing, t]);

  const scrub = useCallback((ms: number) => {
    cancelAnimationFrame(raf.current);
    setPlaying(false);
    setT(ms);
  }, []);

  return { t, end, playing, live: phase === "live", play, scrub };
}
