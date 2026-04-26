import "@crayonai/react-ui/styles/index.css";
import { C1Component, ThemeProvider } from "@thesysai/genui-sdk";

type C1RenderError = {
  code: number;
  c1Response: string;
};

type C1RuntimeProps = {
  c1Response: string;
  onBoundaryAction: (message: string) => void;
  onBoundaryError: (error: C1RenderError) => void;
};

export default function C1Runtime({
  c1Response,
  onBoundaryAction,
  onBoundaryError,
}: C1RuntimeProps) {
  return (
    <ThemeProvider>
      <C1Component
        c1Response={c1Response}
        isStreaming={false}
        onAction={(event) => {
          onBoundaryAction(
            event.humanFriendlyMessage ||
              event.params?.humanFriendlyMessage ||
              "C1 action captured at the renderer boundary.",
          );
        }}
        onError={(error) => onBoundaryError(error)}
      />
    </ThemeProvider>
  );
}
