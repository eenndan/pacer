#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

// Vec3f (the type exercising the ops.hpp operator mixins) now lives in geometry.hpp,
// next to Point — it is a geometry/coordinate vector, not a telemetry sample type.
#include <pacer/geometry/geometry.hpp>

using pacer::Vec3f;

TEST_CASE("Vec3f equality and inequality operators", "[ops][equality]") {
  SECTION("equal vectors compare equal") {
    CHECK(Vec3f{1, 2, 3} == Vec3f{1, 2, 3});
  }

  SECTION("vectors differing in x compare not equal") {
    CHECK_FALSE(Vec3f{9, 2, 3} == Vec3f{1, 2, 3});
  }

  SECTION("vectors differing in y compare not equal") {
    CHECK_FALSE(Vec3f{1, 9, 3} == Vec3f{1, 2, 3});
  }

  SECTION("vectors differing in z compare not equal") {
    CHECK_FALSE(Vec3f{1, 2, 9} == Vec3f{1, 2, 3});
  }

  SECTION("operator!= is false for identical vectors") {
    // This was the key bug: old code returned true for equal vectors.
    CHECK_FALSE(Vec3f{1, 2, 3} != Vec3f{1, 2, 3});
  }

  SECTION("operator!= is true when only one component differs") {
    // The critical regression case: single-component difference must produce
    // !=.
    CHECK(Vec3f{1, 2, 3} != Vec3f{1, 2, 9});
  }

  SECTION("operator!= is true when all components differ") {
    CHECK(Vec3f{1, 2, 3} != Vec3f{9, 9, 9});
  }
}

TEST_CASE("Vec3f addition and subtraction", "[ops][arithmetic]") {
  SECTION("operator+ produces correct element-wise sum") {
    Vec3f result = Vec3f{1, 2, 3} + Vec3f{4, 5, 6};
    CHECK(result == Vec3f{5, 7, 9});
  }

  SECTION("operator- produces correct element-wise difference") {
    Vec3f result = Vec3f{5, 7, 9} - Vec3f{4, 5, 6};
    CHECK(result == Vec3f{1, 2, 3});
  }

  SECTION("operator+= accumulates in place") {
    Vec3f a{1, 2, 3};
    a += Vec3f{4, 5, 6};
    CHECK(a == Vec3f{5, 7, 9});
  }

  SECTION("operator-= subtracts in place") {
    Vec3f a{5, 7, 9};
    a -= Vec3f{4, 5, 6};
    CHECK(a == Vec3f{1, 2, 3});
  }
}

TEST_CASE("Vec3f scalar multiplication and division", "[ops][scalar]") {
  SECTION("vector * scalar") { CHECK(Vec3f{1, 2, 3} * 2.0 == Vec3f{2, 4, 6}); }

  SECTION("scalar * vector (commutative)") {
    CHECK(2.0 * Vec3f{1, 2, 3} == Vec3f{2, 4, 6});
  }

  SECTION("vector / scalar") { CHECK(Vec3f{2, 4, 6} / 2.0 == Vec3f{1, 2, 3}); }

  SECTION("operator*= scales in place") {
    Vec3f a{1, 2, 3};
    a *= 2.0;
    CHECK(a == Vec3f{2, 4, 6});
  }

  SECTION("operator/= scales in place") {
    Vec3f a{2, 4, 6};
    a /= 2.0;
    CHECK(a == Vec3f{1, 2, 3});
  }
}

TEST_CASE("Vec3f dot product (Scalar)", "[ops][dot]") {
  SECTION("member Scalar() computes dot product") {
    double result = Vec3f{1, 2, 3}.Scalar(Vec3f{4, 5, 6});
    // 1*4 + 2*5 + 3*6 = 4 + 10 + 18 = 32
    CHECK(result == Catch::Approx(32.0));
  }

  SECTION("free function Scalar() computes dot product (found via ADL)") {
    Vec3f a{1, 2, 3};
    Vec3f b{4, 5, 6};
    double result = Scalar(a, b);
    CHECK(result == Catch::Approx(32.0));
  }

  SECTION("dot product with zero vector is zero") {
    CHECK(Vec3f{1, 2, 3}.Scalar(Vec3f{0, 0, 0}) == Catch::Approx(0.0));
  }
}

TEST_CASE("Vec3f SquaredNorm", "[ops][norm]") {
  SECTION("3-4-0 triangle gives squared norm 25") {
    CHECK(Vec3f{3, 4, 0}.SquaredNorm() == Catch::Approx(25.0));
  }

  SECTION("zero vector has squared norm 0") {
    CHECK(Vec3f{0, 0, 0}.SquaredNorm() == Catch::Approx(0.0));
  }

  SECTION("unit vector has squared norm 1") {
    CHECK(Vec3f{1, 0, 0}.SquaredNorm() == Catch::Approx(1.0));
  }
}

TEST_CASE("Vec3f Norm (Euclidean length)", "[ops][norm]") {
  SECTION("3-4-0 triangle gives norm 5") {
    CHECK(Vec3f{3, 4, 0}.Norm() == Catch::Approx(5.0));
  }

  SECTION("1-2-2 vector gives norm 3") {
    // sqrt(1 + 4 + 4) = sqrt(9) = 3
    CHECK(Vec3f{1, 2, 2}.Norm() == Catch::Approx(3.0));
  }

  SECTION("zero vector has norm 0") {
    CHECK(Vec3f{0, 0, 0}.Norm() == Catch::Approx(0.0));
  }
}

TEST_CASE("Vec3f pointwise (element-wise) multiply and divide",
          "[ops][pointwise]") {
  SECTION("element-wise multiplication") {
    CHECK(Vec3f{1, 2, 3} * Vec3f{4, 5, 6} == Vec3f{4, 10, 18});
  }

  SECTION("element-wise division") {
    CHECK(Vec3f{4, 10, 18} / Vec3f{4, 5, 6} == Vec3f{1, 2, 3});
  }

  SECTION("element-wise *= in place") {
    Vec3f a{1, 2, 3};
    a *= Vec3f{4, 5, 6};
    CHECK(a == Vec3f{4, 10, 18});
  }

  SECTION("element-wise /= in place") {
    Vec3f a{4, 10, 18};
    a /= Vec3f{4, 5, 6};
    CHECK(a == Vec3f{1, 2, 3});
  }
}
