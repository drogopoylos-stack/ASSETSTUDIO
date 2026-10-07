import StageRunner from "../components/StageRunner";

export default function Studio3D() {
  return (
    <StageRunner
      stage="gen3d"
      title="3D Studio"
      subtitle="Image/text→mesh with TRELLIS/Hunyuan3D (local) or Tripo/Meshy (API). Pick a 2D image as input for image-to-3D."
      inputType="image"
    />
  );
}
