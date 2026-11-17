// Workbench icon set (lucide). `Icon name=…` keeps the chrome consistent:
// one stroke weight, one optical size per context.
import {
  Blocks, Bot, Command, Ellipsis, FileText, FolderKanban, FolderTree, History, Home, Library, MessageSquare,
  PanelBottom, PanelLeft, PanelRight, Plus, ScrollText, Search, Settings, Smartphone, Sparkles, Upload, Wrench, X, FileCode,
  GitCompareArrows, GitCommitHorizontal, MessagesSquare, Globe, MonitorSmartphone, type LucideIcon,
} from "lucide-react";

const ICONS: Record<string, LucideIcon> = {
  library: Library,
  browser: Globe,
  projects: FolderKanban,
  workspace: FolderTree,
  threads: MessagesSquare,
  scm: GitCompareArrows,
  commit: GitCommitHorizontal,
  search: Search,
  plugins: Blocks,
  settings: Settings,
  phone: Smartphone,
  agent: Sparkles,
  bot: Bot,
  chat: MessageSquare,
  tools: Wrench,
  paper: FileText,
  report: ScrollText,
  file: FileCode,
  diff: GitCompareArrows,
  home: Home,
  panel: PanelBottom,
  sidebar: PanelLeft,
  aipane: PanelRight,
  close: X,
  plus: Plus,
  upload: Upload,
  more: Ellipsis,
  command: Command,
  history: History,
  computer: MonitorSmartphone,
};

export function Icon({ name, size = 16, strokeWidth = 1.75 }: { name: string; size?: number; strokeWidth?: number }) {
  const C = ICONS[name] || Ellipsis;
  return <C className="wb-icon" size={size} strokeWidth={strokeWidth} aria-hidden="true" />;
}
