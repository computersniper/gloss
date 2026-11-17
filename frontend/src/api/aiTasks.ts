import { useEffect, useReducer } from "react";
import {
  api,
  type AiArtifactRequest,
  type AiTaskSnapshot,
  type CreateAiTaskRequest,
} from "./client";

const snapshots = new Map<string, AiTaskSnapshot>();
const taskIds = new Map<string, string>();
const errors = new Map<string, string>();
const listeners = new Map<string, Set<() => void>>();
const streams = new Map<string, EventSource>();
const pendingStarts = new Set<string>();
const pendingRestores = new Set<string>();
const restored = new Set<string>();
const featureByKey = new Map<string, string>();
const generations = new Map<string, number>();

const terminal = new Set(["completed", "failed", "cancelled"]);

const globalListeners = new Set<() => void>();

function emit(key: string) {
  listeners.get(key)?.forEach((listener) => listener());
  globalListeners.forEach((listener) => listener());
}

function remember(key: string, snapshot: AiTaskSnapshot) {
  snapshots.set(key, snapshot);
  if (snapshot.id) taskIds.set(key, snapshot.id);
  errors.delete(key);
  emit(key);
}

function invalidate(key: string) {
  generations.set(key, (generations.get(key) || 0) + 1);
}

function clearKey(key: string) {
  invalidate(key);
  streams.get(key)?.close();
  streams.delete(key);
  snapshots.delete(key);
  taskIds.delete(key);
  pendingStarts.delete(key);
  pendingRestores.delete(key);
  restored.delete(key);
  errors.delete(key);
  featureByKey.delete(key);
  emit(key);
}

/** Remove detached UI state after an extension and its local records are deleted. */
export function clearAiTasksForFeature(featureId: string) {
  const keys = [...featureByKey.entries()]
    .filter(([, feature]) => feature === featureId)
    .map(([key]) => key);
  keys.forEach(clearKey);
}

function watch(key: string, task: AiTaskSnapshot) {
  if (!task.id || terminal.has(task.status) || streams.has(key)) return;
  const source = new EventSource(api.aiTaskEventsUrl(task.id));
  streams.set(key, source);
  source.onmessage = (event) => {
    try {
      const next = JSON.parse(event.data) as AiTaskSnapshot;
      remember(key, next);
      if (terminal.has(next.status)) {
        source.close();
        streams.delete(key);
      }
    } catch {
      // Ignore heartbeat/non-snapshot frames.
    }
  };
  source.onerror = () => {
    source.close();
    streams.delete(key);
    const id = taskIds.get(key);
    if (!id) return;
    void api.getAiTask(id).then((next) => {
      remember(key, next);
      if (!terminal.has(next.status)) window.setTimeout(() => watch(key, next), 900);
    }).catch(() => {
      errors.set(key, "Lost connection to the task progress stream.");
      emit(key);
    });
  };
}

export interface AiTaskController {
  task: AiTaskSnapshot | undefined;
  running: boolean;
  error: string | undefined;
  start: (request: CreateAiTaskRequest) => Promise<AiTaskSnapshot | undefined>;
  restore: (request: AiArtifactRequest) => Promise<void>;
  cancel: () => Promise<void>;
  clear: () => void;
}

/** Detached task state: progress survives switching tabs and remounting panels. */
export function useAiTask(key: string | null): AiTaskController {
  const [, bump] = useReducer((value) => value + 1, 0);
  useEffect(() => {
    if (!key) return;
    let set = listeners.get(key);
    if (!set) {
      set = new Set();
      listeners.set(key, set);
    }
    set.add(bump);
    const task = snapshots.get(key);
    if (task) watch(key, task);
    bump();
    return () => {
      set!.delete(bump);
    };
  }, [key]);

  const task = key ? snapshots.get(key) : undefined;
  return {
    task,
    running: !!key && (pendingStarts.has(key) || (!!task && !terminal.has(task.status))),
    error: key ? errors.get(key) : undefined,
    start: async (request) => {
      if (!key) return undefined;
      featureByKey.set(key, request.feature_id);
      restored.add(key);
      errors.delete(key);
      pendingStarts.add(key);
      emit(key);
      try {
        const next = await api.createAiTask(request);
        remember(key, next);
        watch(key, next);
        return next;
      } catch (error: any) {
        errors.set(key, String(error?.message || error));
        emit(key);
        return undefined;
      } finally {
        pendingStarts.delete(key);
        emit(key);
      }
    },
    restore: async (request) => {
      if (!key || restored.has(key) || pendingRestores.has(key) || snapshots.has(key)) {
        return;
      }
      featureByKey.set(key, request.feature_id);
      pendingRestores.add(key);
      const generation = generations.get(key) || 0;
      try {
        const artifact = await api.getAiArtifact(request);
        if (
          artifact.found
          && generation === (generations.get(key) || 0)
          && !snapshots.has(key)
        ) {
          remember(key, {
            id: "",
            feature_id: request.feature_id,
            paper_id: request.paper_id,
            status: "completed",
            progress: 100,
            stage: "completed",
            result: artifact.result,
          });
        }
      } catch {
        // A local cache miss/read failure must never trigger AI or block manual run.
      } finally {
        pendingRestores.delete(key);
        if (generation === (generations.get(key) || 0)) restored.add(key);
        emit(key);
      }
    },
    cancel: async () => {
      if (!key) return;
      const id = taskIds.get(key);
      if (!id) return;
      try {
        const next = await api.cancelAiTask(id);
        remember(key, next);
      } catch (error: any) {
        errors.set(key, String(error?.message || error));
        emit(key);
      }
    },
    clear: () => {
      if (!key) return;
      clearKey(key);
    },
  };
}

export interface AiTaskEntry {
  key: string;
  task: AiTaskSnapshot;
  running: boolean;
}

/** Every AI background task started in this session (cached artifacts excluded). */
export function useAllAiTasks(): AiTaskEntry[] {
  const [, bump] = useReducer((value) => value + 1, 0);
  useEffect(() => {
    globalListeners.add(bump);
    return () => {
      globalListeners.delete(bump);
    };
  }, []);
  return [...snapshots.entries()]
    .filter(([, task]) => !!task.id)
    .map(([key, task]) => ({ key, task, running: !terminal.has(task.status) }))
    .reverse();
}

export async function cancelAiTaskByKey(key: string) {
  const id = taskIds.get(key);
  if (!id) return;
  try {
    remember(key, await api.cancelAiTask(id));
  } catch (error: any) {
    errors.set(key, String(error?.message || error));
    emit(key);
  }
}
