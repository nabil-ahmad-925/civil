export type InstructionRecord = {
  id: string;
  /** Optional short label; the UI falls back to the first line of the text. */
  title: string;
  text: string;
  createdAt: string;
  updatedAt: string;
};

export const MAX_INSTRUCTION_CHARS = 20_000;
export const MAX_TITLE_CHARS = 120;
