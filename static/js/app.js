/* ============================================================
   STATE
============================================================ */
let authToken = localStorage.getItem("auth_token");
let currentUser = null;
let products = [];

/* ============================================================
   API HELPER
============================================================ */
async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

/* ============================================================
   GOOGLE AUTH
============================================================ */
window.handleCredentialResponse = async function (response) {
  try {
    const data = await api("/api/auth/google", {
      method: "POST",
      body: JSON.stringify({ credential: response.credential }),
    });
    authToken = data.token;
    currentUser = data.user;
    localStorage.setItem("auth_token", authToken);
    setLoggedIn(currentUser);
  } catch (e) {
    alert("Login failed: " + e.message);
  }
};

document.getElementById("fakeGoogleBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/auth/google", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        credential: "DEMO_BYPASS",
        demo_email: "admin@shop.com",
        demo_name: "Admin Demo",
      }),
    });
    const data = await res.json();
    authToken = data.token;
    currentUser = data.user;
    localStorage.setItem("auth_token", authToken);
    setLoggedIn(currentUser);
  } catch (e) {
    alert("Demo login failed: " + e.message);
  }
});

/* ============================================================
   LOGIN STATE
============================================================ */
function setLoggedIn(user) {
  currentUser = user;
  const loginView = document.getElementById("loginView");
  const shopView = document.getElementById("shopView");
  const adminView = document.getElementById("adminView");
  const userSection = document.getElementById("userSection");
  const mainNav = document.getElementById("mainNav");
  const adminNavLink = document.getElementById("adminNavLink");
  const adminBadge = document.getElementById("adminBadge");

  if (user) {
    loginView.classList.add("hidden");
    shopView.classList.remove("hidden");
    adminView.classList.add("hidden");
    userSection.classList.remove("hidden");
    mainNav.classList.remove("hidden");

    document.getElementById("userName").textContent = (user.name || "User").split(" ")[0];
    const avatar = document.getElementById("headerAvatar");
    avatar.src = user.picture || "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='1.5'%3E%3Ccircle cx='12' cy='7' r='4'/%3E%3Cpath d='M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2'/%3E%3C/svg%3E";

    if (user.is_admin) {
      adminNavLink.classList.remove("hidden");
      adminBadge.classList.remove("hidden");
    } else {
      adminNavLink.classList.add("hidden");
      adminBadge.classList.add("hidden");
    }

    loadProducts();
  } else {
    loginView.classList.remove("hidden");
    shopView.classList.add("hidden");
    adminView.classList.add("hidden");
    userSection.classList.add("hidden");
    mainNav.classList.add("hidden");
  }
}

document.getElementById("signOutBtn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  authToken = null;
  currentUser = null;
  localStorage.removeItem("auth_token");
  if (window.google?.accounts?.id) window.google.accounts.id.disableAutoSelect();
  setLoggedIn(null);
});

/* ============================================================
   NAVIGATION
============================================================ */
function navigateTo(view) {
  document.querySelectorAll(".nav-link").forEach((el) => el.classList.remove("active"));
  const navBtn = document.querySelector(`[data-view="${view}"]`);
  if (navBtn) navBtn.classList.add("active");

  if (view === "shop") {
    document.getElementById("shopView").classList.remove("hidden");
    document.getElementById("adminView").classList.add("hidden");
    renderShop();
  } else if (view === "admin") {
    if (!currentUser?.is_admin) return alert("Admin only.");
    document.getElementById("shopView").classList.add("hidden");
    document.getElementById("adminView").classList.remove("hidden");
    renderAdmin();
  }
}

/* ============================================================
   PRODUCTS — PUBLIC
============================================================ */
async function loadProducts() {
  try {
    products = await api("/api/products");
    renderShop();
  } catch (e) {
    console.error(e);
  }
}

function renderShop() {
  const grid = document.getElementById("productGrid");
  if (!products.length) {
    grid.innerHTML = '<p class="empty-state">No products available.</p>';
    return;
  }
  grid.innerHTML = products.map((p) => {
    const img = p.image
      ? `<img src="${escapeAttr(p.image)}" alt="${escapeAttr(p.name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='block';">`
      : "";
    const emojiStyle = p.image ? "display:none;" : "display:block;";
    return `
      <div class="product-card">
        <div class="product-image-wrap">
          ${img}
          <span class="emoji-fallback" style="${emojiStyle}">${p.emoji || "📦"}</span>
        </div>
        <div class="product-body">
          <h3>${escapeHtml(p.name)}</h3>
          <div class="product-desc">${escapeHtml(p.description || "")}</div>
          <div class="price-row">
            <span class="price">$${Number(p.price).toFixed(0)}</span>
            <button class="buy-btn" onclick="addToCart('${p.id}')" ${p.stock <= 0 ? "disabled" : ""}>
              ${p.stock <= 0 ? "Out of stock" : "Add to cart"}
            </button>
          </div>
        </div>
      </div>`;
  }).join("");
}

async function addToCart(id) {
  try {
    const res = await api("/api/orders", { method: "POST", body: JSON.stringify({ product_id: id }) });
    alert(`Order placed! ID: ${res.order_id}`);
    await loadProducts();
  } catch (e) {
    alert("Could not place order: " + e.message);
  }
}

/* ============================================================
   ADMIN — RENDER
============================================================ */
async function renderAdmin() {
  try {
    const [stats, orders] = await Promise.all([
      api("/api/admin/stats"),
      api("/api/orders"),
    ]);

    document.getElementById("statProducts").textContent = stats.total_products;
    document.getElementById("statRevenue").textContent = "$" + Math.round(stats.total_revenue);
    document.getElementById("statOrders").textContent = stats.total_orders;
    document.getElementById("statAvg").textContent = "$" + Math.round(stats.avg_order);

    const pBody = document.getElementById("adminProductTable");
    if (!products.length) {
      pBody.innerHTML = '<tr><td colspan="5" class="empty-state">No products yet.</td></tr>';
    } else {
      pBody.innerHTML = products.map((p) => {
        const thumb = p.image
          ? `<img src="${escapeAttr(p.image)}" alt="" onerror="this.parentElement.innerHTML='${p.emoji || "📦"}'">`
          : (p.emoji || "📦");
        return `
          <tr>
            <td><div class="product-cell"><div class="thumb">${thumb}</div><strong>${escapeHtml(p.name)}</strong></div></td>
            <td class="hide-mobile">${escapeHtml(p.description || "")}</td>
            <td>$${Number(p.price).toFixed(2)}</td>
            <td>${p.stock}</td>
            <td>
              <button class="btn-secondary" onclick="editProduct('${p.id}')">Edit</button>
              <button class="btn-danger" onclick="deleteProduct('${p.id}')">Delete</button>
            </td>
          </tr>`;
      }).join("");
    }

    const oBody = document.getElementById("adminOrderTable");
    if (!orders.length) {
      oBody.innerHTML = '<tr><td colspan="5" class="empty-state">No orders yet.</td></tr>';
    } else {
      oBody.innerHTML = orders.map((o) => {
        let cls = "badge-blue";
        if (o.status === "Delivered") cls = "badge-green";
        else if (o.status === "Processing") cls = "badge-yellow";
        return `
          <tr>
            <td><strong>${o.id}</strong></td>
            <td>${escapeHtml(o.customer || "")}</td>
            <td class="hide-mobile">${o.items} item${o.items > 1 ? "s" : ""}</td>
            <td>$${Number(o.total).toFixed(2)}</td>
            <td><span class="badge ${cls}">${o.status}</span></td>
          </tr>`;
      }).join("");
    }
  } catch (e) {
    console.error(e);
    alert("Failed to load admin data: " + e.message);
  }
}

/* ============================================================
   ADMIN — PRODUCT CRUD
============================================================ */
function openProductModal(product = null) {
  document.getElementById("productModal").classList.remove("hidden");
  const fileInput = document.getElementById("productImageFile");
  const urlInput = document.getElementById("productImageUrl");
  const dataInput = document.getElementById("productImageData");

  if (product) {
    document.getElementById("modalTitle").textContent = "Edit Product";
    document.getElementById("productId").value = product.id;
    document.getElementById("productName").value = product.name;
    document.getElementById("productDesc").value = product.description || "";
    document.getElementById("productEmoji").value = product.emoji || "📦";
    document.getElementById("productPrice").value = product.price;
    document.getElementById("productStock").value = product.stock;
    fileInput.value = "";
    dataInput.value = product.image || "";
    urlInput.value = product.image && !product.image.startsWith("data:") ? product.image : "";
    setImagePreview(product.image || "");
  } else {
    document.getElementById("modalTitle").textContent = "Add Product";
    document.getElementById("productForm").reset();
    document.getElementById("productId").value = "";
    dataInput.value = "";
    fileInput.value = "";
    urlInput.value = "";
    setImagePreview("");
  }
}

function closeProductModal() {
  document.getElementById("productModal").classList.add("hidden");
}

async function saveProduct(e) {
  e.preventDefault();
  const id = document.getElementById("productId").value;
  const payload = {
    name: document.getElementById("productName").value.trim(),
    description: document.getElementById("productDesc").value.trim(),
    emoji: document.getElementById("productEmoji").value.trim(),
    price: parseFloat(document.getElementById("productPrice").value),
    stock: parseInt(document.getElementById("productStock").value, 10),
    image: document.getElementById("productImageData").value || document.getElementById("productImageUrl").value.trim() || null,
  };

  try {
    if (id) {
      await api(`/api/products/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/products", { method: "POST", body: JSON.stringify(payload) });
    }
    closeProductModal();
    await loadProducts();
    await renderAdmin();
  } catch (e) {
    alert("Save failed: " + e.message);
  }
}

function editProduct(id) {
  const product = products.find((p) => p.id === id);
  if (product) openProductModal(product);
}

async function deleteProduct(id) {
  const product = products.find((p) => p.id === id);
  if (!confirm(`Delete "${product?.name}"?`)) return;
  try {
    await api(`/api/products/${id}`, { method: "DELETE" });
    await loadProducts();
    await renderAdmin();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

/* ============================================================
   IMAGE HANDLING (uploads to Vercel Blob via /api/upload)
============================================================ */
async function handleImageUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  if (!file.type.startsWith("image/")) return alert("Please select an image.");

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/upload", {
      method: "POST",
      headers: { Authorization: `Bearer ${authToken}` },
      body: formData,
    });
    if (!res.ok) throw new Error("Upload failed");
    const data = await res.json();
    document.getElementById("productImageData").value = data.url;
    document.getElementById("productImageUrl").value = "";
    setImagePreview(data.url);
  } catch (e) {
    alert("Upload failed: " + e.message);
  }
}

function handleImageUrl(url) {
  url = url.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) return;
  document.getElementById("productImageData").value = "";
  document.getElementById("productImageFile").value = "";
  setImagePreview(url);
}

function setImagePreview(src) {
  const preview = document.getElementById("imagePreview");
  if (src) {
    preview.innerHTML = `<img src="${escapeAttr(src)}" alt="preview" onerror="this.parentElement.innerHTML='<span class=&quot;emoji-fallback&quot;>⚠️</span>'">`;
  } else {
    const emoji = document.getElementById("productEmoji").value || "🖼️";
    preview.innerHTML = `<span class="emoji-fallback">${emoji}</span>`;
  }
}

function removeProductImage() {
  document.getElementById("productImageData").value = "";
  document.getElementById("productImageUrl").value = "";
  document.getElementById("productImageFile").value = "";
  setImagePreview("");
}

/* ============================================================
   UTILS
============================================================ */
function escapeHtml(str) {
  return String(str || "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}
function escapeAttr(str) { return escapeHtml(str); }

/* ============================================================
   INIT
============================================================ */
window.addEventListener("load", async () => {
  if (authToken) {
    try {
      const me = await api("/api/auth/me");
      setLoggedIn(me.user);
      return;
    } catch (e) {
      authToken = null;
      localStorage.removeItem("auth_token");
    }
  }
  setLoggedIn(null);

  setTimeout(() => {
    const gsiBtn = document.querySelector(".g_id_signin");
    if (gsiBtn && gsiBtn.children.length === 0 && !document.getElementById("loginView").classList.contains("hidden")) {
      document.getElementById("fallbackGoogleBtn").classList.remove("hidden");
      gsiBtn.style.display = "none";
    }
  }, 2000);
});

document.getElementById("productModal").addEventListener("click", (e) => {
  if (e.target.id === "productModal") closeProductModal();
});
