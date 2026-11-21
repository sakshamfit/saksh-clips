import PromptComposer from "@/components/ui/prompt-composer";

export default function PromptComposerDemo() {
  const models = [
    { id: "gpt-4o", name: "GPT-4o", description: "Great for most tasks", badge: "Fast" },
    { id: "gpt-4o-mini", name: "GPT-4o mini", description: "Cheaper and faster" },
    { id: "o1", name: "o1", description: "Advanced reasoning" },
  ];
  return (
    <div className="flex w-full items-center justify-center p-8">
      <PromptComposer placeholder="Ask anything…" models={models} defaultModelId="gpt-4o" onSend={(text, modelId) => console.log("send", text, modelId)} />
    </div>
  );
}
