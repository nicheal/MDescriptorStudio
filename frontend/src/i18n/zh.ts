// Chinese dictionary. Keys are the English UI literals; values are the
// Simplified Chinese renderings. Missing keys fall back to English at runtime,
// and i18n/index.test.ts fails the build when a key used in the source is not
// listed here.
export const zhDict: Record<string, string> = {};
