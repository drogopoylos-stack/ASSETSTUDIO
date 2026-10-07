/// <reference types="vite/client" />

import type React from "react";

// Allow the <model-viewer> web component (from @google/model-viewer) in JSX.
declare global {
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": React.DetailedHTMLProps<
        React.HTMLAttributes<HTMLElement> & {
          src?: string;
          alt?: string;
          "camera-controls"?: boolean | string;
          "auto-rotate"?: boolean | string;
          "shadow-intensity"?: string | number;
          exposure?: string | number;
          "environment-image"?: string;
          ar?: boolean | string;
          poster?: string;
          loading?: string;
          reveal?: string;
        },
        HTMLElement
      >;
    }
  }
}

export {};
