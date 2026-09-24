// Built into vue-controlled.bundle.js for the isolated JCR-06 browser fixture.
import { createApp, h, ref } from "vue";

const ControlledForm = {
  setup() {
    const name = ref("");
    const generation = ref(0);

    function changeName(event) {
      const value = event.target.value;
      name.value = value;
      const request = new XMLHttpRequest();
      request.open("POST", "/draft", false);
      request.setRequestHeader("Content-Type", "application/json");
      request.send(JSON.stringify({ field: "full_name", value }));
    }

    return () => h("div", [
      h("label", ["Full name ", h("input", {
        id: "name", name: "full_name", required: true,
        value: name.value, onInput: changeName,
      })]),
      h("button", {
        id: "rerender", type: "button",
        onClick: () => { generation.value += 1; },
      }, "Rerender"),
      h("button", { id: "final", type: "button" }, "Submit application"),
      h("output", {
        id: "framework-state", "data-generation": generation.value,
        "data-framework-value": name.value,
      }, name.value),
    ]);
  },
};

createApp(ControlledForm).mount("#root");
