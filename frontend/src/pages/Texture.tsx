import StageRunner from "../components/StageRunner";

export default function Texture() {
  return (
    <StageRunner
      stage="texture"
      title="Texture"
      subtitle="Project / paint textures onto a mesh with TRELLIS2/Hunyuan (local) or Tripo/Meshy (API)."
      inputType="mesh"
      inputRequired
    />
  );
}
