import StageRunner from "../components/StageRunner";

export default function VideoStudio() {
  return <StageRunner stage="video" title="Video"
    subtitle="Write your scene, camera movement and sound in the prompt. MiniMax H3 runs locally in ComfyUI; H3 Max uses the paid fal API (key in Settings). Add an optional starting image. Videos save to your Catalog and can be played or downloaded here."
    inputType="image" />;
}
