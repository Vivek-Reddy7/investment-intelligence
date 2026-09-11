/** @type {import('next').NextConfig} */
export default {
  // The API is read-only by construction (ADR 006, invariant 9). Nothing here
  // should ever need a mutating HTTP method.
  experimental: {},
};
