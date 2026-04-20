/// <reference types="vite/client" />

// Feature 079 Phase 3：Vite define 注入的构建指纹。
// - production build：timestamp + short sha（如 1776691234-ad2b7b0）
// - dev / test：固定为 "dev"，避免触发版本漂移告警
declare const __BUILD_ID__: string;
