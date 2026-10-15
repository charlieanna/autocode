"use strict";
const count = document.getElementById("count");
document.getElementById("run").addEventListener("click", () => {
  count.textContent = String(Number(count.textContent) + 1);
});
