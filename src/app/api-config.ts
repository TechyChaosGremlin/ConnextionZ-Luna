/**
 * Backend API configuration
 * Point to the Python GraphQL backend for auth and profile data
 */
export const BACKEND_API_URL = import.meta.env?.VITE_API_URL || "http://127.0.0.1:8002";

export const GRAPHQL_ENDPOINT = `${BACKEND_API_URL}/graphql`;
export const AUTH_LOGIN_ENDPOINT = `${BACKEND_API_URL}/auth/login`;
export const AUTH_REGISTER_ENDPOINT = `${BACKEND_API_URL}/auth/register`;
export const AUTH_REFRESH_ENDPOINT = `${BACKEND_API_URL}/auth/refresh`;
export const AUTH_LOGOUT_ENDPOINT = `${BACKEND_API_URL}/auth/logout`;
