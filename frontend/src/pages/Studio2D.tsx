import StageRunner from "../components/StageRunner";

export default function Studio2D() {
  return (
    <StageRunner
      stage="image2d"
      title="2D Studio"
      subtitle="Text→image with Qwen/Flux/SDXL (local) or nanobanana/gpt-image-1 (API). Optional image input for edits."
      inputType="image"
    />
  );
}
