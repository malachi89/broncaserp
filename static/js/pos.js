(function () {
  const productButtons = Array.from(document.querySelectorAll(".product-tile"));
  const searchInput = document.getElementById("barcode-input");
  const cartLines = document.getElementById("cart-lines");
  const cartTotal = document.getElementById("cart-total");
  const cartJson = document.getElementById("cart-json");
  const posForm = document.getElementById("pos-form");
  const clearCart = document.getElementById("clear-cart");
  const paymentMethod = document.getElementById("payment-method");
  const customerField = document.getElementById("customer-field");
  const posFeedback = document.getElementById("pos-feedback");
  const cart = new Map();

  function normalize(value) {
    return String(value || "").trim().toLowerCase();
  }

  function productFromButton(button) {
    return {
      id: Number(button.dataset.productId),
      name: button.dataset.name,
      barcode: button.dataset.barcode,
      sku: button.dataset.sku,
      price: Number(button.dataset.price),
      stock: Number(button.dataset.stock),
    };
  }

  function money(value) {
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: "MXN",
    }).format(value);
  }

  function showFeedback(message, level) {
    if (!posFeedback) return;
    posFeedback.textContent = message;
    posFeedback.classList.remove("is-hidden", "error", "warn");
    posFeedback.classList.add(level || "warn");
  }

  function clearFeedback() {
    if (!posFeedback) return;
    posFeedback.textContent = "";
    posFeedback.classList.add("is-hidden");
    posFeedback.classList.remove("error", "warn");
  }

  function syncStockFeedback() {
    const oversoldLine = Array.from(cart.values()).find(({ product, quantity }) => quantity > product.stock);
    if (oversoldLine) {
      const { product } = oversoldLine;
      showFeedback(
        product.stock <= 0
          ? product.name + " ya no tiene existencias. La venta se registrara de todos modos."
          : product.name + " solo tiene " + product.stock + " en existencia. La venta se registrara de todos modos.",
        "warn"
      );
      return;
    }
    clearFeedback();
  }

  function addProduct(product) {
    const current = cart.get(product.id) || { product, quantity: 0 };
    current.quantity += 1;
    cart.set(product.id, current);
    renderCart();
  }

  function findProduct(query) {
    const clean = normalize(query);
    if (!clean) return null;
    const exact = productButtons.find((button) => {
      const product = productFromButton(button);
      return normalize(product.barcode) === clean || normalize(product.sku) === clean;
    });
    if (exact) return productFromButton(exact);
    const partial = productButtons.find((button) => normalize(button.dataset.name).includes(clean));
    return partial ? productFromButton(partial) : null;
  }

  function renderCart() {
    cartLines.innerHTML = "";
    let total = 0;
    cart.forEach(({ product, quantity }) => {
      total += product.price * quantity;
      const line = document.createElement("div");
      line.className = "cart-line";
      line.innerHTML = `
        <strong title="${product.name}">${product.name}</strong>
        <input type="number" min="0.001" step="0.001" value="${quantity}">
        <span>${money(product.price * quantity)}</span>
        <button type="button" title="Quitar">x</button>
      `;
      const input = line.querySelector("input");
      input.addEventListener("change", () => {
        const nextQuantity = Number(input.value);
        if (!nextQuantity || nextQuantity <= 0) {
          cart.delete(product.id);
        } else {
          cart.set(product.id, { product, quantity: nextQuantity });
        }
        renderCart();
      });
      line.querySelector("button").addEventListener("click", () => {
        cart.delete(product.id);
        renderCart();
      });
      cartLines.appendChild(line);
    });
    cartTotal.textContent = money(total);
    cartJson.value = JSON.stringify(
      Array.from(cart.values()).map(({ product, quantity }) => ({
        product_id: product.id,
        quantity,
      }))
    );
    syncStockFeedback();
  }

  productButtons.forEach((button) => {
    button.addEventListener("click", () => addProduct(productFromButton(button)));
  });

  searchInput.addEventListener("input", () => {
    const query = normalize(searchInput.value);
    productButtons.forEach((button) => {
      const product = productFromButton(button);
      const visible =
        !query ||
        normalize(product.name).includes(query) ||
        normalize(product.barcode).includes(query) ||
        normalize(product.sku).includes(query);
      button.hidden = !visible;
    });
  });

  searchInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const product = findProduct(searchInput.value);
    if (product) {
      addProduct(product);
      searchInput.value = "";
      productButtons.forEach((button) => (button.hidden = false));
    }
  });

  paymentMethod.addEventListener("change", () => {
    customerField.classList.toggle("is-hidden", paymentMethod.value !== "credit");
  });

  clearCart.addEventListener("click", () => {
    cart.clear();
    clearFeedback();
    renderCart();
    searchInput.focus();
  });

  posForm.addEventListener("submit", (event) => {
    if (cart.size === 0) {
      event.preventDefault();
      showFeedback("Agrega productos al ticket.", "error");
    }
  });

  renderCart();
})();
