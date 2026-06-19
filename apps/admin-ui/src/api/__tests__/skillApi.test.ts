/**
 * SkillApi adapter tests — skill-authoring-ia Phase B.
 *
 * The adapter is mostly thin delegation; the one piece of real logic is the
 * platform ``renameSupportingFile`` which the server has no route for, so it
 * is composed put-new + delete-old (put first so a failure leaves the
 * original intact).
 */
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import * as skills from "../skills";
import * as platform from "../platform-skills";
import { tenantSkillApi, platformSkillApi } from "../skillApi";

vi.mock("../skills");
vi.mock("../platform-skills");

afterEach(() => {
  vi.clearAllMocks();
});

describe("tenantSkillApi", () => {
  it("delegates each method to the tenant ./skills SDK", async () => {
    (skills.getSkill as Mock).mockResolvedValue({ id: "s1" });
    await tenantSkillApi.getSkill("s1");
    expect(skills.getSkill).toHaveBeenCalledWith("s1");

    (skills.deleteSupportingFile as Mock).mockResolvedValue({ version: 3 });
    await tenantSkillApi.deleteSupportingFile("s1", 2, "references/a.md");
    expect(skills.deleteSupportingFile).toHaveBeenCalledWith("s1", 2, "references/a.md");
  });
});

describe("platformSkillApi", () => {
  it("delegates reads/writes to the platform SDK", async () => {
    (platform.getPlatformSkill as Mock).mockResolvedValue({ id: "p1" });
    await platformSkillApi.getSkill("p1");
    expect(platform.getPlatformSkill).toHaveBeenCalledWith("p1");
  });

  it("composes rename as put-new then delete-old (using the put's version)", async () => {
    (platform.putPlatformSupportingFile as Mock).mockResolvedValue({ version: 5 });
    (platform.deletePlatformSupportingFile as Mock).mockResolvedValue({ version: 6 });

    const result = await platformSkillApi.renameSupportingFile(
      "p1",
      4,
      "references/old.md",
      "references/new.md",
      { content: "Zm9v", size: 3, mime: "text/plain" },
    );

    // put writes the NEW path onto the base version (4).
    expect(platform.putPlatformSupportingFile).toHaveBeenCalledWith("p1", 4, "references/new.md", {
      content: "Zm9v",
      size: 3,
      mime: "text/plain",
    });
    // delete removes the OLD path from the version the put produced (5).
    expect(platform.deletePlatformSupportingFile).toHaveBeenCalledWith("p1", 5, "references/old.md");
    // returns the delete result (the latest version).
    expect(result).toEqual({ version: 6 });
  });

  it("does not delete if the put fails (original left intact)", async () => {
    (platform.putPlatformSupportingFile as Mock).mockRejectedValue(new Error("boom"));
    await expect(
      platformSkillApi.renameSupportingFile("p1", 4, "a/old.md", "a/new.md", {
        content: "Zm9v",
        size: 3,
        mime: "text/plain",
      }),
    ).rejects.toThrow("boom");
    expect(platform.deletePlatformSupportingFile).not.toHaveBeenCalled();
  });
});
