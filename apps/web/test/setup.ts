// Setup global test frontend: matcher DOM (toBeInTheDocument, toHaveTextContent, …).
//
// testing-library hanya mendaftarkan `cleanup` otomatis bila `globals: true`;
// di sini tidak, jadi didaftarkan manual. Tanpa ini DOM antar test menumpuk
// dan assertion gagal dengan "Found multiple elements".
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
