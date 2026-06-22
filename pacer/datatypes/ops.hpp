#pragma once

// Small CRTP vector-algebra mixins. A concrete N-component type derives from
// one of these and only has to expose element access through `operator[]`; in
// return it inherits the arithmetic operators, a dot product (`Scalar`) and
// Euclidean magnitude. Every reduction walks indices 0..N-1 in ascending order,
// so the floating-point accumulation is deterministic down to the bit.

#include <cmath>
#include <cstddef>

namespace pacer {

// Adds componentwise +/- and -, scaling by a `T`, the dot product and the norm.
//   Derived — the concrete type (CRTP self-type)
//   T       — the element type
//   N       — number of components
template <typename Derived, typename T, size_t N> struct LinearOperators {
private:
  Derived &self() { return static_cast<Derived &>(*this); }
  const Derived &self() const { return static_cast<const Derived &>(*this); }

public:
  bool operator==(const Derived &rhs) const {
    for (size_t i = 0; i < N; ++i)
      if (self()[i] != rhs[i])
        return false;
    return true;
  }
  bool operator!=(const Derived &rhs) const { return !(*this == rhs); }

  Derived &operator+=(const Derived &rhs) {
    for (size_t i = 0; i < N; ++i)
      self()[i] += rhs[i];
    return self();
  }
  Derived &operator-=(const Derived &rhs) {
    for (size_t i = 0; i < N; ++i)
      self()[i] -= rhs[i];
    return self();
  }
  Derived &operator*=(T k) {
    for (size_t i = 0; i < N; ++i)
      self()[i] *= k;
    return self();
  }
  Derived &operator/=(T k) {
    for (size_t i = 0; i < N; ++i)
      self()[i] /= k;
    return self();
  }

  friend Derived operator+(Derived lhs, const Derived &rhs) {
    return lhs += rhs;
  }
  friend Derived operator-(Derived lhs, const Derived &rhs) {
    return lhs -= rhs;
  }
  friend Derived operator*(Derived lhs, T k) {
    return lhs.LinearOperators::operator*=(k);
  }
  friend Derived operator*(T k, Derived rhs) { return rhs *= k; }
  friend Derived operator/(Derived lhs, T k) {
    return lhs.LinearOperators::operator/=(k);
  }

  // Dot product, accumulated from the lowest index up.
  T Scalar(const Derived &rhs) const {
    T acc = self()[0] * rhs[0];
    for (size_t i = 1; i < N; ++i)
      acc += self()[i] * rhs[i];
    return acc;
  }
  friend T Scalar(const Derived &lhs, const Derived &rhs) {
    return lhs.Scalar(rhs);
  }

  /// Squared length (skips the sqrt — use when only comparing magnitudes).
  T SquaredNorm() const { return Scalar(self()); }
  /// Euclidean length.
  T Norm() const { return std::sqrt(SquaredNorm()); }
};

// Hadamard (elementwise) product and quotient — only meaningful for genuine
// vectors, so it is a separate mixin from the linear-space operations above.
template <typename Derived, typename T, size_t N> struct PointwiseOperators {
private:
  Derived &self() { return static_cast<Derived &>(*this); }

public:
  Derived &operator*=(const Derived &rhs) {
    for (size_t i = 0; i < N; ++i)
      self()[i] *= rhs[i];
    return self();
  }
  Derived &operator/=(const Derived &rhs) {
    for (size_t i = 0; i < N; ++i)
      self()[i] /= rhs[i];
    return self();
  }

  friend Derived operator*(Derived lhs, const Derived &rhs) {
    return lhs.PointwiseOperators::operator*=(rhs);
  }
  friend Derived operator/(Derived lhs, const Derived &rhs) {
    return lhs.PointwiseOperators::operator/=(rhs);
  }
};

// A full vector: both the linear-space algebra and the elementwise products.
// The compound-assignment overloads are re-declared here purely to disambiguate
// the scalar form (-> LinearOperators) from the vector form (->
// PointwiseOperators).
template <typename Derived, typename T, size_t N>
struct VectorOperators : LinearOperators<Derived, T, N>,
                         PointwiseOperators<Derived, T, N> {
  Derived &operator*=(T k) {
    return static_cast<Derived *>(this)->LinearOperators::operator*=(k);
  }
  Derived &operator*=(const Derived &rhs) {
    return static_cast<Derived *>(this)->PointwiseOperators::operator*=(rhs);
  }
  Derived &operator/=(T k) {
    return static_cast<Derived *>(this)->LinearOperators::operator/=(k);
  }
  Derived &operator/=(const Derived &rhs) {
    return static_cast<Derived *>(this)->PointwiseOperators::operator/=(rhs);
  }
};

} // namespace pacer
