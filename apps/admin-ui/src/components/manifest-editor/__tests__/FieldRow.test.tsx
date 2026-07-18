import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FieldRow } from "../FieldRow";

describe("FieldRow", () => {
  it("always renders the label and the one-line brief", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault
        defaultValue="30"
      >
        <input />
      </FieldRow>,
    );

    expect(screen.getByText("最大迭代次数")).toBeInTheDocument();
    expect(screen.getByText("限制单次运行的最大步数")).toBeInTheDocument();
  });

  it("renders the control passed as children", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault
        defaultValue="30"
      >
        <input aria-label="max-iterations-input" />
      </FieldRow>,
    );

    expect(screen.getByLabelText("max-iterations-input")).toBeInTheDocument();
  });

  it("exposes data-field-id on the row root for the given fieldId", () => {
    const { container } = render(
      <FieldRow
        fieldId="workflow.timeout_seconds"
        label="超时时间"
        brief="单次运行的最长秒数"
        isDefault
        defaultValue="600"
      >
        <input />
      </FieldRow>,
    );

    expect(
      container.querySelector('[data-field-id="workflow.timeout_seconds"]'),
    ).toBeInTheDocument();
  });

  it("keeps the impact note collapsed until it is expanded", async () => {
    const user = userEvent.setup();
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        impact="调大会增加单次运行时长与成本;调小可能导致任务提前中断"
        isDefault
        defaultValue="30"
      >
        <input />
      </FieldRow>,
    );

    expect(
      screen.queryByText("调大会增加单次运行时长与成本;调小可能导致任务提前中断"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByText("影响说明"));

    expect(
      screen.getByText("调大会增加单次运行时长与成本;调小可能导致任务提前中断"),
    ).toBeInTheDocument();
  });

  it("renders no expander at all when impact is omitted", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault
        defaultValue="30"
      >
        <input />
      </FieldRow>,
    );

    expect(screen.queryByText("影响说明")).not.toBeInTheDocument();
  });

  it("shows a gray '默认 <value>' badge when isDefault is true", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault
        defaultValue="30"
      >
        <input />
      </FieldRow>,
    );

    const badge = screen.getByText("默认 30");
    expect(badge).toBeInTheDocument();
    expect(badge.closest(".ant-tag")).not.toHaveClass("ant-tag-blue");
  });

  it("shows a blue current-value badge (no prefix) when isDefault is false", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault={false}
        defaultValue="45"
      >
        <input />
      </FieldRow>,
    );

    expect(screen.queryByText("默认 45")).not.toBeInTheDocument();
    const badge = screen.getByText("45");
    expect(badge.closest(".ant-tag")).toHaveClass("ant-tag-blue");
  });

  it("renders no badge when defaultValue is omitted", () => {
    render(
      <FieldRow
        fieldId="workflow.max_iterations"
        label="最大迭代次数"
        brief="限制单次运行的最大步数"
        isDefault
      >
        <input />
      </FieldRow>,
    );

    expect(document.querySelector(".ant-tag")).not.toBeInTheDocument();
  });
});
