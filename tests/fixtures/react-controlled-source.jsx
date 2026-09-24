// Built into react-controlled.bundle.js for the isolated JCR-06 browser fixture.
import React, { useState } from "react";
import { createRoot } from "react-dom/client";

function ControlledForm() {
  const [name, setName] = useState("");
  const [generation, setGeneration] = useState(0);

  function changeName(event) {
    const value = event.target.value;
    setName(value);
    const request = new XMLHttpRequest();
    request.open("POST", "/draft", false);
    request.setRequestHeader("Content-Type", "application/json");
    request.send(JSON.stringify({ field: "full_name", value }));
  }

  return <>
    <label>Full name <input id="name" name="full_name" required
      value={name} onChange={changeName} /></label>
    <button id="rerender" type="button"
      onClick={() => setGeneration(current => current + 1)}>Rerender</button>
    <button id="final" type="button">Submit application</button>
    <output id="framework-state" data-generation={generation}
      data-framework-value={name}>{name}</output>
  </>;
}

createRoot(document.getElementById("root")).render(<ControlledForm />);
