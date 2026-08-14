import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  MiniMap,
  Controls,
  BaseEdge,
  Handle,
  Position,
  type EdgeProps,
  type EdgeTypes,
  type NodeProps,
  type NodeTypes,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { MindNode } from "../api/client";
import { useAiTask } from "../api/aiTasks";
import { useStore } from "../store";
import { AgentTaskCard } from "./AgentTaskCard";
import {
  MIND_NODE_WIDTH,
  MIND_X_STEP,
  getMindEdgeGeometry,
} from "../mindMapGeometry.mjs";

// Hard cap on how many tree nodes we lay out (keeps huge maps readable + fast).
const MAX_NODES = 80;
// Left-bar color by depth (fallback when a node has no kind).
const DEPTH_COLORS = ["var(--accent2)", "var(--accent)", "#4fb8a0", "#7f8aa0"];
function colorForDepth(depth: number): string {
  return DEPTH_COLORS[Math.min(depth, DEPTH_COLORS.length - 1)];
}

// Primary coloring: by semantic KIND. Fallback to depth-based color when absent.
const KIND_COLOR: Record<string, string> = {
  root: "#a78bfa", problem: "#ef9a9a", method: "#90caf9", result: "#ffd54f",
  concept: "#ce93d8", contribution: "#a5d6a7", background: "#ffcc80",
  experiment: "#80deea", limitation: "#ef6b6b", data: "#b0bec5",
  context: "#ffb74d", source: "#81c784", theory: "#ba68c8", argument: "#64b5f6",
  interpretation: "#4dd0e1", evidence: "#aed581",
};
function colorForKind(kind: string | undefined, depth: number): string {
  if (kind && KIND_COLOR[kind]) return KIND_COLOR[kind];
  return colorForDepth(depth);
}

// ---------------------------------------------------------------------------
// Custom card node
// ---------------------------------------------------------------------------
interface MindNodeData {
  title: string;
  kind?: string;
  summary?: string;
  depth: number;
  isRoot: boolean;
  hasChildren: boolean;
  collapsed: boolean;
  focused: boolean;
  faded: boolean;
  [key: string]: unknown;
}

const hiddenHandle = {
  opacity: 0,
  width: 6,
  height: 6,
  minWidth: 6,
  minHeight: 6,
  border: "none",
  background: "transparent",
  pointerEvents: "none" as const,
};

function MindCardImpl({ data }: NodeProps) {
  const d = data as unknown as MindNodeData;
  const bar = colorForKind(d.kind, d.depth);
  return (
    <div
      style={{
        position: "relative",
        width: MIND_NODE_WIDTH,
        boxSizing: "border-box",
        background: "var(--bg2)",
        border: "1px solid var(--border)",
        borderLeft: `4px solid ${bar}`,
        borderRadius: 10,
        padding: "6px 10px",
        fontSize: 12.5,
        lineHeight: 1.3,
        color: "var(--fg)",
        fontWeight: d.isRoot ? 700 : 400,
        opacity: d.faded ? 0.25 : 1,
        boxShadow: d.focused
          ? "0 0 0 2px var(--accent), 0 0 16px rgba(122,162,247,.5)"
          : "none",
        transition: "opacity .18s ease, box-shadow .18s ease",
        cursor: "pointer",
      }}
    >
      <Handle type="target" position={Position.Left} style={hiddenHandle} />
      <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        {d.hasChildren && (
          <span
            style={{
              flex: "0 0 auto",
              marginTop: 1,
              fontSize: 10,
              lineHeight: "16px",
              color: bar,
            }}
          >
            {d.collapsed ? "▸" : "▾"}
          </span>
        )}
        <div style={{ minWidth: 0 }}>
          {d.kind && (
            <div
              style={{
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: bar,
                marginBottom: 1,
              }}
            >
              {d.kind}
            </div>
          )}
          <div
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 3,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
              wordBreak: "break-word",
            }}
          >
            {d.title || "·"}
          </div>
          {d.summary && (
            <div
              style={{
                marginTop: 3,
                fontSize: 11,
                lineHeight: 1.35,
                color: "var(--fg-dim)",
                fontWeight: 400,
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                wordBreak: "break-word",
              }}
            >
              {d.summary}
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} style={hiddenHandle} />
    </div>
  );
}
const MindCard = memo(MindCardImpl);

function MindEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  markerEnd,
  style,
  interactionWidth,
}: EdgeProps) {
  const { path } = getMindEdgeGeometry(sourceX, sourceY, targetX, targetY);
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      style={style}
      interactionWidth={interactionWidth}
    />
  );
}
const MindEdge = memo(MindEdgeImpl);

// Registered at module scope so React Flow never sees a new object each render.
const nodeTypes: NodeTypes = { mind: MindCard };
const edgeTypes: EdgeTypes = { mind: MindEdge };

// ---------------------------------------------------------------------------
// Tree -> flat structure with stable ids (independent of collapse)
// ---------------------------------------------------------------------------
interface FlatNode {
  id: string;
  title: string;
  kind?: string;
  summary?: string;
  depth: number;
  parentId: string | null;
  childIds: string[];
}
interface Flat {
  byId: Map<string, FlatNode>;
  rootId: string | null;
  truncated: boolean;
}

function buildFlat(tree: MindNode | null): Flat {
  const byId = new Map<string, FlatNode>();
  let counter = 0;
  let truncated = false;

  function walk(node: MindNode, depth: number, parentId: string | null): string | null {
    if (counter >= MAX_NODES) {
      truncated = true;
      return null;
    }
    const id = "n" + counter;
    counter++;
    const flat: FlatNode = {
      id,
      title: (node.title ?? "").toString(),
      kind: node.kind ? node.kind.toString() : undefined,
      summary: node.summary ? node.summary.toString() : undefined,
      depth,
      parentId,
      childIds: [],
    };
    byId.set(id, flat);
    const children = node.children || [];
    for (const child of children) {
      if (counter >= MAX_NODES) {
        truncated = true;
        break;
      }
      const childId = walk(child, depth + 1, id);
      if (childId) flat.childIds.push(childId);
    }
    return id;
  }

  const rootId = tree ? walk(tree, 0, null) : null;
  return { byId, rootId, truncated };
}

// ---------------------------------------------------------------------------
// Flat + collapsed -> laid-out React Flow nodes/edges (post-order leaf packing)
// ---------------------------------------------------------------------------
function layout(flat: Flat, collapsed: Set<string>): { nodes: Node[]; edges: Edge[] } {
  const { byId, rootId } = flat;
  if (!rootId) return { nodes: [], edges: [] };

  // Estimate each card's rendered height so taller (summary-bearing) cards don't
  // overlap: leaves are packed by cumulative height; internal nodes are centered
  // on their children. `centerById` holds vertical CENTERS (converted to top later).
  const NODE_GAP = 36;
  function estH(f: any): number {
    const titleLines = Math.min(3, Math.max(1, Math.ceil((f.title?.length || 0) / 24)));
    return Math.max(54, 18 + (f.kind ? 14 : 0) + titleLines * 17 + (f.summary ? 34 : 0));
  }

  const centerById = new Map<string, number>();
  let cursorY = 0;

  function place(id: string): number {
    const node = byId.get(id)!;
    const kids = collapsed.has(id) ? [] : node.childIds;
    let center: number;
    if (kids.length === 0) {
      const h = estH(node);
      center = cursorY + h / 2;
      cursorY += h + NODE_GAP;
    } else {
      const childCenters = kids.map(place);
      center = (childCenters[0] + childCenters[childCenters.length - 1]) / 2;
    }
    centerById.set(id, center);
    return center;
  }
  place(rootId);

  const nodes: Node[] = [];
  const edges: Edge[] = [];
  for (const [id, center] of centerById) {
    const f = byId.get(id)!;
    nodes.push({
      id,
      type: "mind",
      position: { x: f.depth * MIND_X_STEP, y: center - estH(f) / 2 },
      data: {
        title: f.title,
        kind: f.kind,
        summary: f.summary,
        depth: f.depth,
        isRoot: f.parentId === null,
        hasChildren: f.childIds.length > 0,
        collapsed: collapsed.has(id),
        focused: false,
        faded: false,
      } as MindNodeData,
    });
    // Edge from parent -> this node (only when parent is also visible).
    if (f.parentId && centerById.has(f.parentId)) {
      edges.push({
        id: `e-${f.parentId}-${id}`,
        source: f.parentId,
        target: id,
        type: "mind",
      });
    }
  }
  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Panel
// ---------------------------------------------------------------------------
export function MindMapPanel() {
  const current = useStore((s) => s.current);
  const uiLang = useStore((s) => s.uiLang);
  const T = uiLang === "zh"
    ? {
        regenerate: "↻ 重新生成",
        mapping: "生成中…",
        updating: "更新中…",
        openPrompt: "打开一篇论文以查看其概念图。",
        building: "正在生成概念图…",
        none: "尚无概念图。",
        empty: "本论文的概念图为空。",
        truncated: (n: number) => `仅显示前 ${n} 个节点。`,
        clearFocus: "点击空白处取消聚焦。",
        close: "关闭",
        connections: "关联",
        parent: "父节点",
        children: (n: number) => `子节点 (${n})`,
      }
    : {
        regenerate: "↻ Regenerate",
        mapping: "Mapping…",
        updating: "Updating…",
        openPrompt: "Open a paper to see its concept map.",
        building: "Building the concept map…",
        none: "No concept map yet.",
        empty: "This paper has an empty concept map.",
        truncated: (n: number) => `Showing the first ${n} nodes.`,
        clearFocus: "Click empty space to clear focus.",
        close: "Close",
        connections: "Connections",
        parent: "Parent",
        children: (n: number) => `Children (${n})`,
      };
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const outputLanguage = useStore((state) => state.outputLanguage);

  const mmKey = current
    ? `mindmap:${current.id}:${current.reading_lens || "auto"}:${outputLanguage}`
    : null;
  const { task, running, error: err, start, restore, cancel } = useAiTask(mmKey);
  useEffect(() => {
    if (!current) return;
    void restore({
      feature_id: "core.mindmap",
      paper_id: current.id,
      language: outputLanguage,
    });
  }, [mmKey]);
  const taskResult = task?.result as { tree?: MindNode } | MindNode | null | undefined;
  const tree = taskResult && "tree" in taskResult ? taskResult.tree || null : taskResult as MindNode | null;
  const regenerate = () => {
    if (!current) return;
    void start({
      feature_id: "core.mindmap",
      paper_id: current.id,
      refresh: !!tree,
      language: outputLanguage,
    });
  };

  // reset the interactive view state when the paper changes
  useEffect(() => {
    setCollapsed(new Set());
    setFocusedId(null);
  }, [current?.id]);

  // Stable id assignment (only depends on the tree).
  const flat = useMemo(() => buildFlat(tree), [tree]);

  // Base layout: recomputes on tree + collapse changes only.
  const base = useMemo(() => layout(flat, collapsed), [flat, collapsed]);

  // Focus highlight set: focused node + its ancestors + its direct children.
  const highlight = useMemo(() => {
    const s = new Set<string>();
    if (!focusedId) return s;
    s.add(focusedId);
    let cur = flat.byId.get(focusedId);
    while (cur && cur.parentId) {
      s.add(cur.parentId);
      cur = flat.byId.get(cur.parentId);
    }
    const f = flat.byId.get(focusedId);
    if (f) for (const cid of f.childIds) s.add(cid);
    return s;
  }, [focusedId, flat]);

  // Inject focus/fade flags without triggering a relayout.
  const nodes = useMemo<Node[]>(() => {
    if (!focusedId) return base.nodes;
    return base.nodes.map((n) => ({
      ...n,
      data: {
        ...(n.data as MindNodeData),
        focused: n.id === focusedId,
        faded: !highlight.has(n.id),
      },
    }));
  }, [base.nodes, focusedId, highlight]);

  const edges = useMemo<Edge[]>(() => {
    return base.edges.map((e) => {
      const active = !!focusedId && (e.source === focusedId || e.target === focusedId);
      return {
        ...e,
        animated: false,
        style: {
          stroke: active ? "var(--accent)" : "var(--border)",
          strokeWidth: active ? 2 : 1.5,
          opacity: focusedId ? (active ? 1 : 0.14) : 0.78,
          transition: "stroke .18s ease, opacity .18s ease",
        },
      };
    });
  }, [base.edges, focusedId]);

  const onNodeClick = useCallback(
    (_evt: React.MouseEvent, node: Node) => {
      setFocusedId(node.id);
      const info = flat.byId.get(node.id);
      if (info && info.childIds.length > 0) {
        setCollapsed((prev) => {
          const next = new Set(prev);
          if (next.has(node.id)) next.delete(node.id);
          else next.add(node.id);
          return next;
        });
      }
    },
    [flat],
  );

  const clearFocus = useCallback(() => setFocusedId(null), []);

  // Select a node from the details drawer: reuse the focus mechanism and make
  // sure the node is visible by expanding its (possibly collapsed) parent.
  const selectNode = useCallback(
    (id: string) => {
      setFocusedId(id);
      const info = flat.byId.get(id);
      if (info && info.parentId) {
        const parentId = info.parentId;
        setCollapsed((prev) => {
          if (!prev.has(parentId)) return prev;
          const next = new Set(prev);
          next.delete(parentId);
          return next;
        });
      }
    },
    [flat],
  );

  let body: React.ReactNode;
  if (!current) {
    body = (
      <div className="muted" style={{ flex: 1, padding: 16 }}>
        {T.openPrompt}
      </div>
    );
  } else if (!tree) {
    body = (
      <div className="muted" style={{ flex: 1, padding: 16 }}>
        {T.none}
      </div>
    );
  } else if (!flat.rootId) {
    body = (
      <div className="muted" style={{ flex: 1, padding: 16 }}>
        {T.empty}
      </div>
    );
  } else {
    // Details-drawer data: selected node (== focused) + its connections.
    const selected = focusedId ? flat.byId.get(focusedId) ?? null : null;
    const parent =
      selected && selected.parentId ? flat.byId.get(selected.parentId) ?? null : null;
    const children = selected
      ? (selected.childIds
          .map((cid) => flat.byId.get(cid))
          .filter(Boolean) as FlatNode[])
      : [];
    const connRow = (n: FlatNode) => (
      <button
        key={n.id}
        onClick={() => selectNode(n.id)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          textAlign: "left",
          background: n.id === focusedId ? "var(--bg2)" : "none",
          border: "1px solid var(--border)",
          borderRadius: 7,
          padding: "5px 8px",
          marginBottom: 4,
          color: "var(--fg)",
          fontSize: 12,
          cursor: "pointer",
        }}
      >
        <span
          style={{
            flex: "0 0 auto",
            width: 8,
            height: 8,
            borderRadius: 2,
            background: colorForKind(n.kind, n.depth),
          }}
        />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {n.title || "·"}
        </span>
      </button>
    );

    body = (
      <div style={{ position: "relative", flex: 1, minHeight: 0 }}>
        <div style={{ position: "absolute", inset: 0 }}>
          <ReactFlow
            key={current.id}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            minZoom={0.1}
            maxZoom={2}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            onNodeClick={onNodeClick}
            onPaneClick={clearFocus}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={20} size={1} color="#222a38" />
            <MiniMap
              pannable
              zoomable
              nodeColor={(n) =>
                colorForKind(
                  (n.data as MindNodeData)?.kind,
                  (n.data as MindNodeData)?.depth ?? 0,
                )
              }
              style={{ background: "var(--bg2)" }}
            />
            <Controls />
          </ReactFlow>
        </div>
        {selected && (
          <div
            style={{
              position: "absolute",
              top: 0,
              right: 0,
              bottom: 0,
              width: 260,
              background: "var(--panel)",
              borderLeft: "1px solid var(--border)",
              overflowY: "auto",
              padding: 12,
              zIndex: 5,
            }}
          >
            <button
              onClick={clearFocus}
              title={T.close}
              style={{
                position: "absolute",
                top: 8,
                right: 8,
                background: "none",
                border: "none",
                color: "var(--fg-dim)",
                fontSize: 16,
                lineHeight: 1,
                cursor: "pointer",
              }}
            >
              ×
            </button>
            {selected.kind && (
              <span
                style={{
                  display: "inline-block",
                  fontSize: 9,
                  fontWeight: 700,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  color: "var(--bg2)",
                  background: colorForKind(selected.kind, selected.depth),
                  borderRadius: 5,
                  padding: "2px 6px",
                  marginBottom: 6,
                }}
              >
                {selected.kind}
              </span>
            )}
            <div
              style={{
                fontWeight: 700,
                fontSize: 14,
                lineHeight: 1.3,
                color: "var(--fg)",
                paddingRight: 16,
                wordBreak: "break-word",
              }}
            >
              {selected.title || "·"}
            </div>
            {selected.summary && (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 12.5,
                  lineHeight: 1.45,
                  color: "var(--fg-dim)",
                  wordBreak: "break-word",
                }}
              >
                {selected.summary}
              </div>
            )}
            {(parent || children.length > 0) && (
              <div style={{ marginTop: 14 }}>
                <div
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    letterSpacing: "0.08em",
                    textTransform: "uppercase",
                    color: "var(--fg-dim)",
                    marginBottom: 6,
                  }}
                >
                  {T.connections}
                </div>
                {parent && (
                  <>
                    <div style={{ fontSize: 10, color: "var(--fg-dim)", margin: "6px 0 3px" }}>
                      {T.parent}
                    </div>
                    {connRow(parent)}
                  </>
                )}
                {children.length > 0 && (
                  <>
                    <div style={{ fontSize: 10, color: "var(--fg-dim)", margin: "8px 0 3px" }}>
                      {T.children(children.length)}
                    </div>
                    {children.map((c) => connRow(c))}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className="panel-body"
      style={{ display: "flex", flexDirection: "column", padding: 0 }}
    >
      <div
        className="panel-actions"
        style={{ padding: "8px 12px", display: "flex", alignItems: "center", gap: 10 }}
      >
        <button onClick={regenerate} disabled={running || !current}>
          {tree ? T.regenerate : (uiLang === "zh" ? "生成概念图" : "Build concept map")}
        </button>
        {running && tree && <span className="muted">{T.updating}</span>}
        {flat.truncated && (
          <span className="muted" style={{ fontSize: 11 }}>
            {T.truncated(MAX_NODES)}
          </span>
        )}
        {focusedId && (
          <span className="muted" style={{ fontSize: 11 }}>
            {T.clearFocus}
          </span>
        )}
      </div>
      {err && <div className="error" style={{ padding: "0 12px 8px" }}>{err}</div>}
      {task && (running || task.status === "failed" || task.status === "cancelled") && (
        <div style={{ padding: "0 12px 10px" }}>
          <AgentTaskCard
            task={task}
            uiLang={uiLang}
            agent={{
              name: "Concept Cartographer",
              name_zh: "概念制图师",
              icon: "🗺️",
              messages: {
                generating: { en: "Building the concept map", zh: "正在构建概念图" },
              },
            }}
            onCancel={running ? () => void cancel() : undefined}
          />
        </div>
      )}
      {body}
    </div>
  );
}
