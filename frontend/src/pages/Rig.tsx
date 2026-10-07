import StageRunner from "../components/StageRunner";

export default function Rig() {
  return (
    <StageRunner
      stage="rig"
      title="Rig & Animate"
      subtitle="Auto-rig a mesh with Blender/UniRig (local) or Tripo auto-rig / Mixamo (service)."
      inputType="mesh"
      inputRequired
    />
  );
}
