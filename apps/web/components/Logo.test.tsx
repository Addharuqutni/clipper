// Simbol ClipperAI — mengunci geometri "Cut Frame" supaya perubahan yang
// tidak sengaja (mis. sudut jadi 44 derajat) ketahuan. Angka di sini adalah
// master yang sama dengan `app/icon.svg`.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Logo } from "@/components/Logo";

const FULL = "M69 24H154L186 56V232H69Z M99 54H142L156 68V202H99Z";
const SMALL = "M69 24H154L186 56V232H69Z";

describe("Logo", () => {
  it("memakai geometri master: potongan 45 derajat, rasio 9:16", () => {
    const { container } = render(<Logo />);
    const svg = container.querySelector("svg");
    const path = container.querySelector("path");

    expect(svg?.getAttribute("viewBox")).toBe("69 24 117 208");
    expect(path?.getAttribute("d")).toBe(FULL);
  });

  it("menyediakan gambar small tanpa jendela untuk ukuran 16-24 px", () => {
    const { container } = render(<Logo detail="small" />);
    expect(container.querySelector("path")?.getAttribute("d")).toBe(SMALL);
  });

  it("skalanya proporsional terhadap tinggi yang diminta", () => {
    const { container } = render(<Logo size={117} />);
    const svg = container.querySelector("svg");
    // 117 lebar -> tinggi 208 (rasio 117:208).
    expect(svg?.getAttribute("height")).toBe("208");
  });
});
