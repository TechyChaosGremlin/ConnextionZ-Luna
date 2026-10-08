/**
 * Backend API configuration
 * Deployed frontend and backend share a Vercel domain. Keep API requests
 * same-origin there; local development still targets the local FastAPI server.
 */
const configuredBackendUrl = import.meta.env?.VITE_API_URL?.trim().replace(/\/+$/, "");

export const BACKEND_API_URL =
  import.meta.env?.PROD ? "" : (configuredBackendUrl || "http://127.0.0.1:8002");

export const GRAPHQL_ENDPOINT = `${BACKEND_API_URL}/graphql`;
export const AUTH_LOGIN_ENDPOINT = `${BACKEND_API_URL}/auth/login`;
export const AUTH_REGISTER_ENDPOINT = `${BACKEND_API_URL}/auth/register`;
export const AUTH_REFRESH_ENDPOINT = `${BACKEND_API_URL}/auth/refresh`;
export const AUTH_LOGOUT_ENDPOINT = `${BACKEND_API_URL}/auth/logout`;
