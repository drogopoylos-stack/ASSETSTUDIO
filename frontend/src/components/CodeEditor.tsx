import Editor, { loader, type OnMount } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import jsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import cssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import htmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import tsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

// Bundle Monaco (the editor core from VS Code) + its language workers LOCALLY, so
// the editor works fully offline — no CDN. This gives real VS Code editing: Ctrl+F/H
// find & replace, command palette (F1), minimap, folding, multi-cursor, bracket
// matching, and IntelliSense for JS/TS/JSON/CSS/HTML.
(self as any).MonacoEnvironment = {
  getWorker(_: unknown, label: string) {
    if (label === "json") return new jsonWorker();
    if (label === "css" || label === "scss" || label === "less") return new cssWorker();
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker();
    if (label === "typescript" || label === "javascript") return new tsWorker();
    return new editorWorker();
  },
};
loader.config({ monaco });

const LANG: Record<string, string> = {
  ts: "typescript", tsx: "typescript", mts: "typescript", cts: "typescript",
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  py: "python", pyw: "python", json: "json", jsonc: "json",
  css: "css", scss: "scss", less: "less",
  html: "html", htm: "html", xml: "xml", svg: "xml", vue: "html",
  md: "markdown", markdown: "markdown",
  sh: "shell", bash: "shell", zsh: "shell", ps1: "powershell",
  yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini", env: "ini",
  sql: "sql", rs: "rust", go: "go", c: "c", h: "c", cpp: "cpp", hpp: "cpp", cc: "cpp",
  java: "java", cs: "csharp", php: "php", rb: "ruby", swift: "swift", kt: "kotlin",
  dockerfile: "dockerfile", graphql: "graphql", gql: "graphql", r: "r", lua: "lua",
};

function langFor(ext?: string): string {
  return LANG[(ext || "").replace(/^\./, "").toLowerCase()] || "plaintext";
}

// Real VS Code editor. Keeps the same value/onChange/onSave contract the Workspace
// already uses, so file open / dirty / disk-sync all keep working.
export function CodeEditor({ value, onChange, onSave, ext, fontSize = 13 }: {
  value: string;
  onChange: (v: string) => void;
  onSave?: () => void;
  ext?: string;
  fontSize?: number;
}) {
  const onMount: OnMount = (editor, m) => {
    editor.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.KeyS, () => onSave?.());
  };
  return (
    <Editor
      height="100%"
      theme="vs-dark"
      language={langFor(ext)}
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={onMount}
      loading={<div className="h-full flex items-center justify-center text-muted text-xs">loading editor…</div>}
      options={{
        fontSize,
        minimap: { enabled: true },
        automaticLayout: true,
        scrollBeyondLastLine: false,
        wordWrap: "on",
        tabSize: 2,
        smoothScrolling: true,
        cursorBlinking: "smooth",
        renderWhitespace: "selection",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        padding: { top: 8 },
        scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
      }}
    />
  );
}
