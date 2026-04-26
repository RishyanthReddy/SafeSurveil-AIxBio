import { useEffect, useState } from "react";

export type ApiResource<T> =
  | { status: "loading"; data: null; error: null }
  | { status: "success"; data: T; error: null }
  | { status: "error"; data: null; error: string };

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unable to load API resource.";
}

export function useApiResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
): ApiResource<T> {
  const [resource, setResource] = useState<ApiResource<T>>({
    status: "loading",
    data: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    setResource({ status: "loading", data: null, error: null });

    loader(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) {
          setResource({ status: "success", data, error: null });
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setResource({ status: "error", data: null, error: errorMessage(error) });
        }
      });

    return () => {
      controller.abort();
    };
  }, dependencies);

  return resource;
}
