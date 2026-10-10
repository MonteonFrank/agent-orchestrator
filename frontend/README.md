# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    # Agent Orchestrator Frontend

    React + TypeScript with Vite, Tailwind CSS v3, Lucide icons, and TanStack Query.
    Use Node.js 22.12+ (or a current supported LTS) and npm.

    ## Development

    From this directory:

    ```sh
    npm ci
    npm run dev
    ```

    Run the FastAPI backend on port 8000 in a separate terminal. Vite forwards `/v1`
    requests to `http://localhost:8000`; the browser uses relative API URLs.
    The proxy is development-only. Production hosting must route `/v1` to the API.

    The dashboard lists in-memory runs, opens a New Run modal, and polls selected
    run status. Pipeline Steps and Diff Viewer tabs display agent history and
    file proposals. The amber checkpoint banner supports approval, rejection, and
    feedback-driven revisions with optimistic updates and rollback on errors.
    The backend currently generates mock proposals. The requested "Approve & Commit"
    button submits an approval decision only: it does not apply patches or make a
    Git commit. All runs disappear when the backend restarts.

    ## Validation

    ```sh
    npm run build
    npm run lint
    npm test
    ```

    `src/types/orchestrator.ts` mirrors the backend JSON contract: lowercase union
    values, nullable fields, ISO datetime strings, and `Record<string, unknown>` for
    dynamic context. Request fields with server defaults may be omitted.
    TypeScript types do not perform runtime response validation.

    `src/api/client.ts` supplies typed fetchers for creation, run state, diffs, lists,
    and approval decisions. HTTP failures throw `ApiError` with a status code and
    backend detail when available. `fetchRuns()` returns a `Run[]` response from
    GET `/v1/runs`, ordered newest first by the in-memory backend.

    `useRun(runId)` polls every two seconds only while queued or running.
    `useRunDiffs(runId)` fetches the structured diffs response. Both accept a null
    or undefined selection and disable fetching until an ID exists.
    `useSubmitApproval().mutate({ runId, checkpointId, payload })` submits a decision
    and immediately updates run/list caches, restores them on failure, then
    invalidates run state, diffs, and lists to reconcile with the server.

    `src/lib/utils.ts` supplies `cn` through clsx and tailwind-merge. The query
    provider is configured in `src/main.tsx`. Tailwind v3 uses PostCSS and scans
    all source components.

    The current Tailwind v3 dependency tree has seven npm audit advisories in
    development/build dependencies. npm proposes upgrading to v4, which is outside
    this project's requested version. Avoid processing untrusted build inputs and
    review updates before deploying build infrastructure. Runtime dependencies
    can be checked separately with `npm audit --omit=dev`.
      parserOptions: {
