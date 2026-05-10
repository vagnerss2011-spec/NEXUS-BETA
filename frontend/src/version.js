// __APP_VERSION__ é injetado pelo Vite (define em vite.config.js) lendo
// package.json em build time. Fallback 'dev' cobre cenários onde a
// constante não foi substituída (ex.: ambiente de teste sem o build Vite).
/* global __APP_VERSION__ */
export const APP_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'
